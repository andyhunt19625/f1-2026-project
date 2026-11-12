import json
import os
import tempfile
import unittest
from pathlib import Path

from race_engineer import ers_analysis
from race_engineer.state import LiveState
from tests.test_ers_analysis import build


class LiveLapTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "parsed.jsonl")
        build(self.path)
        self.log = Path(self.dir.name) / "live_laps.txt"
        self.state = LiveState(lap_log=self.log)
        with open(self.path) as f:
            for line in f:
                pkt = json.loads(line)
                for ld in pkt.get("lapData", []):   # fields the other live tools read
                    ld.update(numPitStops=0, pitLaneTimerActive=0, pitLaneTimeInLaneInMS=0)
                self.state.ingest(pkt)

    def tearDown(self):
        self.dir.cleanup()

    def test_report_after_each_lap(self):
        laps = self.state.live_laps
        self.assertEqual([r["lap"] for r in laps.reports], ["lap 1", "lap 2"])
        first, second = laps.reports
        self.assertIn("First clean lap", first["text"])
        self.assertEqual(second["gap_to_best_ms"], 0)
        self.assertAlmostEqual(second["battery"]["store_min_MJ"], 3.0, delta=0.01)
        self.assertIn("Battery 4.00 ->", second["text"])
        self.assertEqual(second["vs_ai"]["driver"], "SAINZ")
        self.assertEqual(self.state.counts["live_laps_errors"], 0)
        self.assertIn("lap 2: 0:20.000", self.log.read_text())
        self.assertIn("lap 2", laps.last()["answer"])

    def test_ai_deploy_zones(self):
        res = self.state.live_laps.ai_deploy()
        self.assertEqual(res["cars"], ["SAINZ"])
        self.assertEqual([(z["from_m"], z["to_m"]) for z in res["ai_zones"]], [(500, 1000)])
        self.assertEqual(res["ai_zones"][0]["avg_kw"], 100)
        self.assertEqual(res["ai_zones"][0]["you_avg_kw"], 0)
        self.assertEqual([(z["from_m"], z["to_m"]) for z in res["your_zones"]], [(0, 500)])
        # same answer from the saved-session tool
        off = ers_analysis.ai_deploy(ers_analysis.load(self.path))
        self.assertEqual(off["ai_zones"], res["ai_zones"])

    def test_ai_laps_pruned(self):
        b = self.state.live_laps.builders[1]
        self.assertLessEqual(len(b.laps), 2)


class ReminderTests(unittest.TestCase):
    RULE = [{"when": ["qualifying", "practice"], "check": "ers_mode_none", "text": "Use mode none."}]

    def run_session(self, session_type, mode):
        from race_engineer.live_laps import LiveLaps
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "parsed.jsonl")
            build(p)
            ll = LiveLaps(Path(d) / "log.txt", playbook=self.RULE)
            with open(p) as f:
                for line in f:
                    pkt = json.loads(line)
                    if pkt["packet_name"] == "Session":
                        pkt["sessionType"] = session_type
                    for st in pkt.get("carStatusData", []):
                        st["ersDeployMode"] = mode
                    ll.ingest(pkt)
            return ll

    def test_qualifying_on_hotlap_mode_reminds_every_lap(self):
        ll = self.run_session(5, 2)
        for r in ll.reports:
            self.assertIn("ERS mode: hotlap 100%", r["text"])
            self.assertIn("REMINDER: Use mode none. This lap was on mode none only 0%", r["text"])
        self.assertIn("Use mode none.", ll.reminders()["answer"])

    def test_qualifying_on_mode_none_reminds_once(self):
        ll = self.run_session(5, 0)
        self.assertIn("REMINDER: Use mode none.", ll.reports[0]["text"])
        self.assertNotIn("REMINDER", ll.reports[1]["text"])

    def test_no_reminder_in_race_or_mode_line_in_time_trial(self):
        self.assertNotIn("REMINDER", self.run_session(15, 2).reports[1]["text"])
        tt = self.run_session(18, 2)
        self.assertNotIn("ERS mode", tt.reports[1]["text"])
        self.assertNotIn("REMINDER", tt.reports[1]["text"])


if __name__ == "__main__":
    unittest.main()
