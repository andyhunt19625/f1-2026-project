import json
import tempfile
import unittest
from pathlib import Path

from race_engineer import tools
from race_engineer.state import LiveState


def ld(lap, last_ms, pos, stops=0, pit=0, lane=0, lane_ms=0, front_ms=0):
    return {"currentLapNum": lap, "lastLapTimeInMS": last_ms, "carPosition": pos, "numPitStops": stops,
            "pitStatus": pit, "pitLaneTimerActive": lane, "pitLaneTimeInLaneInMS": lane_ms,
            "deltaToCarInFrontMSPart": front_ms % 60000, "deltaToCarInFrontMinutesPart": front_ms // 60000,
            "deltaToRaceLeaderMSPart": 0, "deltaToRaceLeaderMinutesPart": 0,
            "lapDistance": 10.0, "currentLapTimeInMS": 0, "currentLapInvalid": 0, "driverStatus": 4}


def race(state, track_id=0):
    """You (slot 0) lead; SAINZ (slot 1) does 80 s laps, pits on laps 5 (in-lap 90 s) and
    6 (out-lap 95 s), numPitStops 0 -> 1, 22.5 s in the pit lane. Expected loss 185 - 160 = 25 s."""
    state.ingest({"packet_name": "Session", "sessionType": 15, "trackId": track_id, "trackLength": 5000})
    state.ingest({"packet_name": "Participants", "numActiveCars": 2,
                  "participants": [{"aiControlled": 0, "name": "NORRIS"}, {"aiControlled": 1, "name": "SAINZ"}]})
    times = {1: 85000, 2: 80000, 3: 80000, 4: 81000, 5: 90000, 6: 95000, 7: 80000}

    def send(me, him):
        state.ingest({"packet_name": "LapData", "lapData": [me, him]})

    for lap in range(1, 9):
        last = times.get(lap - 1, 0)
        stops = 1 if lap >= 6 else 0
        in_lane = lap == 6     # still in the pit lane as lap 6 starts
        send(ld(lap, last, 1), ld(lap, last, 2, stops=0 if lap <= 5 else 1, pit=int(in_lane), lane=int(in_lane),
                                  lane_ms=12000 if in_lane else 0, front_ms=30000))
        if lap == 5:     # end of lap 5: into the pit lane, timer runs over the line
            send(ld(5, last, 1), ld(5, last, 2, pit=1, lane=1, lane_ms=10000, front_ms=30000))
        if lap == 6:     # start of lap 6: stop made, still in the lane, then out
            send(ld(6, last, 1), ld(6, last, 2, stops=1, pit=1, lane=1, lane_ms=22500, front_ms=30000))
            send(ld(6, last, 1), ld(6, last, 2, stops=1, pit=0, lane=0, front_ms=30000))


class PitLossTests(unittest.TestCase):
    def test_measured_stored_and_used_next_race(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "circuit_data.json"
            s = LiveState(circuit_store_path=store)
            race(s)
            self.assertEqual(len(s.pit_losses), 1)
            self.assertEqual(s.pit_losses[0]["driver"], "SAINZ")
            self.assertAlmostEqual(s.pit_losses[0]["loss_s"], 25.0)
            data = json.loads(store.read_text())
            self.assertEqual(data["0"]["pit_losses"][0]["loss_s"], 25.0)
            self.assertEqual(data["0"]["pit_lane_ms"][0]["ms"], 22500)
            self.assertEqual(data["0"]["pit_losses"][0]["session_type"], 15)
            ans = tools.pit_loss(s)
            self.assertIn("25.0", ans["answer"])
            # next race at the same track: nobody has pitted yet, the stored value is used
            s2 = LiveState(circuit_store_path=store)
            s2.ingest({"packet_name": "Session", "sessionType": 15, "trackId": 0, "trackLength": 5000})
            s2.ingest({"packet_name": "Participants", "numActiveCars": 2, "participants": [
                {"aiControlled": 0, "name": "NORRIS"}, {"aiControlled": 1, "name": "SAINZ"}]})
            s2.ingest({"packet_name": "LapData", "lapData": [ld(3, 80000, 1), ld(3, 80000, 2, front_ms=31000)]})
            w = tools.pit_window(s2, None, 5.0)
            self.assertEqual(w["pit_loss_s"], 25.0)
            self.assertIn("earlier races at this track", w["pit_loss_source"])
            self.assertTrue(w["in_window"])          # 31 - 25 = 6 s clear, buffer 5
            # a different track has nothing stored
            s3 = LiveState(circuit_store_path=store)
            s3.ingest({"packet_name": "Session", "sessionType": 15, "trackId": 3, "trackLength": 5000})
            s3.ingest({"packet_name": "Participants", "numActiveCars": 2, "participants": [
                {"aiControlled": 0, "name": "NORRIS"}, {"aiControlled": 1, "name": "SAINZ"}]})
            s3.ingest({"packet_name": "LapData", "lapData": [ld(3, 80000, 1), ld(3, 80000, 2, front_ms=31000)]})
            with self.assertRaises(tools.ToolError):
                tools.pit_window(s3, None, 5.0)

    def test_garage_visit_is_not_stored(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "circuit_data.json"
            s = LiveState(circuit_store_path=store)
            s.ingest({"packet_name": "Session", "sessionType": 1, "trackId": 0, "trackLength": 5000})
            s.ingest({"packet_name": "Participants", "numActiveCars": 1,
                      "participants": [{"aiControlled": 0, "name": "NORRIS"}]})
            s.ingest({"packet_name": "LapData", "lapData": [ld(2, 80000, 1, pit=1, lane=1, lane_ms=9000)]})
            s.ingest({"packet_name": "LapData", "lapData": [ld(2, 80000, 1)]})   # numPitStops never went up
            self.assertFalse(store.exists())

    def test_no_store_path_means_nothing_written(self):
        s = LiveState()
        race(s)
        self.assertEqual(len(s.pit_losses), 1)       # still measured for this race


if __name__ == "__main__":
    unittest.main()
