import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from race_engineer import drill
from race_engineer.server import make_server
from race_engineer.state import LiveState
from tests.test_ers_analysis import build


class FakeFeed:
    received = 0
    errors = 0


class DrillTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        p = os.path.join(self.dir.name, "parsed.jsonl")
        build(p)
        self.pkts = []
        for line in open(p):
            pkt = json.loads(line)
            for ld in pkt.get("lapData", []):
                ld.update(numPitStops=0, pitLaneTimerActive=0, pitLaneTimeInLaneInMS=0, carPosition=1,
                          deltaToRaceLeaderMSPart=0, deltaToRaceLeaderMinutesPart=0,
                          deltaToCarInFrontMSPart=0, deltaToCarInFrontMinutesPart=0)
            self.pkts.append(pkt)
        self.state = LiveState(lap_log=Path(self.dir.name) / "laps.txt")
        self.feed = FakeFeed()
        self.httpd = make_server(self.state, self.feed, "127.0.0.1", 0, {"pit_loss": 20.0})
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.t = [1000.0]
        self.log = Path(self.dir.name) / "drill.jsonl"
        self.d = drill.Drill(self.log, port=self.httpd.server_address[1], clock=lambda: self.t[0])

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.dir.cleanup()

    def feed_until(self, n):
        for pkt in self.pkts[:n]:
            self.state.ingest(pkt)
            self.feed.received += 1
        self.pkts = self.pkts[n:]

    def events(self):
        return [json.loads(l) for l in open(self.log) if '"event"' in l]

    def test_lap_report_seen_and_idle_detected(self):
        mid_lap1 = 2 + 3 * 50          # header lines + 50 frames of lap 1
        self.feed_until(mid_lap1)
        self.d.step()
        self.t[0] += 3
        self.feed_until(3 * 100)        # past the start of lap 2: lap 1 report exists
        self.d.step()
        self.t[0] += 3
        self.d.step()                   # nothing new arrived
        ev = self.events()
        laps = [e for e in ev if e["event"] == "lap_report"]
        self.assertEqual(len(laps), 1)
        self.assertEqual(laps[0]["lap"], 1)
        self.assertIn("idle_no_packets", [e["event"] for e in ev])
        text = drill.summary(self.log)
        self.assertIn("last_lap", text)
        self.assertIn("gap_to", text)
        self.assertIn("Lap reports: 1 found", text)

    def test_unreachable_engineer_is_an_event_not_a_crash(self):
        d = drill.Drill(self.log, port=1, clock=lambda: self.t[0])
        d.step()
        self.assertEqual(self.events()[-1]["event"], "engineer_unreachable")


    def test_drying_forecast_and_field_tyres_logged(self):
        self.feed_until(2 + 3 * 50)
        self.state.ingest({"packet_name": "Session", "sessionType": 15, "trackLength": 1000, "weather": 3,
                           "weatherForecastSamples": [
                               {"sessionType": 15, "timeOffset": 0, "weather": 3, "rainPercentage": 60},
                               {"sessionType": 15, "timeOffset": 5, "weather": 2, "rainPercentage": 20}]})
        statuses = [{"visualTyreCompound": 7}, {"visualTyreCompound": 17}]
        self.state.ingest({"packet_name": "CarStatus", "frameIdentifier": 99999, "carStatusData": statuses})
        self.d.step()
        self.d.step()                      # same forecast again: logged once only
        ev = self.events()
        dry = [e for e in ev if e["event"] == "drying_soon"]
        self.assertEqual(len(dry), 1)
        self.assertEqual(dry[0]["in_minutes"], 5)
        tyres = [e for e in ev if e["event"] == "field_tyres"]
        self.assertEqual(len(tyres), 1)
        self.assertEqual((tyres[0]["yours"], tyres[0]["others_on_dry"], tyres[0]["others_total"]),
                         ("intermediate", 1, 1))
        self.assertIn("Drying forecast", drill.summary(self.log))


if __name__ == "__main__":
    unittest.main()
