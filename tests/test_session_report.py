import json
import math
import os
import tempfile
import unittest
from pathlib import Path

from race_engineer import session_report
from tests.test_ers_analysis import build as build_ers


def make_session(folder, session_type, ai_ms, weekend=77, weather=0):
    """Two cars on a 1000 m circle: you 20 s laps, the AI `ai_ms` laps (spread evenly), plus Motion,
    a Session packet for the weekend, car setups and one event."""
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, "base.jsonl")
    build_ers(tmp)
    r = 1000 / (2 * math.pi)
    out = []
    with open(tmp) as f:
        for line in f:
            d = json.loads(line)
            if d["packet_name"] == "Session":
                d.update(sessionType=session_type, trackId=0, weekendLinkIdentifier=weekend, weather=weather,
                         safetyCarStatus=0, aiDifficulty=90, totalLaps=3)
            if d["packet_name"] == "LapData":
                ai = d["lapData"][1]
                ai["currentLapTimeInMS"] = int(ai["currentLapTimeInMS"] * ai_ms / 20000)
                if ai["lastLapTimeInMS"]:
                    ai["lastLapTimeInMS"] = ai_ms
                ang = d["lapData"][0]["lapDistance"] / 1000 * 2 * math.pi
                out.append(d)
                out.append({"packet_name": "Motion", "frameIdentifier": d["frameIdentifier"], "carMotionData": [
                    {"worldPositionX": r * math.cos(ang), "worldPositionZ": r * math.sin(ang)}] * 2})
                continue
            out.append(d)
    os.remove(tmp)
    out.append({"packet_name": "CarSetups", "carSetupData": [{"frontWing": 22, "rearWing": 12}, {"frontWing": 27, "rearWing": 24}]})
    out.append({"packet_name": "Event", "eventCode": "SCAR", "details": {"safetyCarType": 1, "eventType": 0}})
    with open(os.path.join(folder, "parsed.jsonl"), "w") as f:
        f.write("\n".join(json.dumps(x) for x in out) + "\n")


class SessionReportTests(unittest.TestCase):
    def test_report_map_setup_events_and_pace_prediction(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "pace.json"
            out = Path(d) / "reports"
            make_session(os.path.join(d, "20261114T100000Z_p1"), 1, 20500)
            make_session(os.path.join(d, "20261114T120000Z_p2"), 2, 20300)
            res1, _ = session_report.run(os.path.join(d, "20261114T100000Z_p1"), out, store)
            self.assertEqual(res1["session"], "Practice 1")
            self.assertEqual(res1["track"], "Melbourne")
            self.assertEqual(res1["your_ms"], 20000)
            self.assertEqual(res1["ai_ms"], 20500)
            self.assertAlmostEqual(sum(s["gain_ms"] for s in res1["segments"]), 500, delta=30)
            self.assertGreater(len(res1["points"]), 50)
            self.assertEqual(res1["setup_differences"]["rearWing"], {"you": 12, "them": 24})
            self.assertIn("Full safety car deployed.", [e["text"] for e in res1["events"]])
            self.assertIn("weakest", res1["rule_of_thumb"])
            self.assertTrue(res1["pace"]["prediction"]["note"])  # one practice only: not enough yet
            res2, page = session_report.run(os.path.join(d, "20261114T120000Z_p2"), out, store)
            sp = res2["since_previous"]
            self.assertEqual(sp["previous"], "Practice 1")
            self.assertEqual((sp["your_then_ms"], sp["your_now_ms"], sp["gain_ms"]), (20000, 20000, 0))
            self.assertEqual((sp["ai_best_then_ms"], sp["ai_best_now_ms"]), (20500, 20300))
            self.assertEqual(sp["setup_changes"], {})
            self.assertEqual(len(sp["segments"]), 10)
            m = res2["pace"]["prediction"]["methods"]
            self.assertEqual(len(m), 1)
            self.assertEqual(m[0]["ai_quali_ms"], 20100)         # 20500 -> 20300, one more step of -200
            self.assertIn("practice trend", m[0]["method"])
            html = page.read_text()
            self.assertNotIn("__DATA__", html)
            self.assertIn('"ai_ms":20300', html)
            self.assertTrue((out / "20261114T120000Z_p2.json").is_file())
            # second run loads the digest instead of reading the file again
            res3, _ = session_report.run(os.path.join(d, "20261114T120000Z_p2"), out, store)
            self.assertTrue(res3["digest_cached"])

    def test_other_weekend_ratio_used(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "pace.json"
            store.write_text(json.dumps([
                {"folder": "a_p3", "weekend_id": 1, "track_id": 3, "session_type": 3, "conditions": "dry",
                 "ai_best_ms": 80000, "ai_top5_median_ms": 80500, "my_best_ms": 80200},
                {"folder": "a_q", "weekend_id": 1, "track_id": 3, "session_type": 8, "conditions": "dry",
                 "ai_best_ms": 79200, "ai_top5_median_ms": 79600, "my_best_ms": 79000}]))
            make_session(os.path.join(d, "20261114T100000Z_p1"), 1, 20500)
            res, _ = session_report.run(os.path.join(d, "20261114T100000Z_p1"), Path(d) / "r", store)
            m = res["pace"]["prediction"]["methods"]
            self.assertEqual(len(m), 1)
            self.assertEqual(m[0]["ai_quali_ms"], round(20500 * 79200 / 80000))
            self.assertIn("other weekends (1)", m[0]["method"])


if __name__ == "__main__":
    unittest.main()
