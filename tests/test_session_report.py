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


def fuel_digest(burn, wet=False):
    laps = {}
    fuel = 30.0
    for n in range(1, 8):
        laps[(1, n, 0)] = {"fuel_start": fuel, "time_ms": 90000, "pit": n == 1, "valid": True, "mix": 1}
        fuel -= burn
    laps[(1, 8, 0)] = {"fuel_start": fuel, "time_ms": None, "pit": False, "valid": True, "mix": 1}
    return {"meta": {"player": 0, "track_id": 2, "session_type": 1, "total_laps": 5}, "laps": [laps],
            "weather": [(0.0, 3 if wet else 0, 20, 25)], "safety_car": []}


class FuelSectionTests(unittest.TestCase):
    def test_fuel_summary_table(self):
        f = session_report.fuel_summary(fuel_digest(1.4))
        d = f["conditions"]["dry"]
        self.assertAlmostEqual(d["burn_kg"], 1.4, places=3)
        self.assertEqual(d["laps"], 6)
        self.assertEqual(d["need_kg"]["7"], round(1.4 * 9, 1))          # 7 laps + formation + margin
        self.assertEqual(d["need_kg"]["25"], round(1.4 * 27, 1))
        self.assertEqual(f["assumptions"], {"formation_laps": 1, "margin_laps": 1.0})
        self.assertNotIn("wet", f["conditions"])
        self.assertIn("wet", session_report.fuel_summary(fuel_digest(1.1, wet=True))["conditions"])
        self.assertIsNone(session_report.fuel_summary({"meta": {"player": 0, "track_id": 0, "session_type": 1,
                                                                 "total_laps": 1}, "laps": [{}], "weather": [],
                                                       "safety_car": []}))


class SetupSuggestionTests(unittest.TestCase):
    def test_front_wing_halfway_only_when_high_and_losing_on_straights_or_slow(self):
        diff = {"frontWing": {"you": 35, "them": 27}, "rearWing": {"you": 23, "them": 24}}
        s = session_report.setup_suggestions(diff, {"straight": 1133, "slow": 143, "medium": 77, "fast": 2})
        self.assertEqual(len(s), 1)
        self.assertEqual((s[0]["try"], s[0]["you"], s[0]["ai"]), (31, 35, 27))
        self.assertIn("straights and slow corners", s[0]["why"])
        self.assertIn("Rule of thumb, not measured", s[0]["why"])
        self.assertEqual(session_report.setup_suggestions(diff, {"straight": -300, "slow": 50}), [])      # not losing
        self.assertEqual(session_report.setup_suggestions({"frontWing": {"you": 29, "them": 27}}, {"straight": 500}), [])
        self.assertEqual(session_report.setup_suggestions({}, {"straight": 500}), [])


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

    def test_restarts_listed_and_compared_with_last_different_session(self):
        with tempfile.TemporaryDirectory() as d:
            store, out = Path(d) / "pace.json", Path(d) / "reports"
            make_session(os.path.join(d, "20261114T100000Z_aa"), 1, 20500)                  # practice
            for i, (ai, wing) in enumerate(((20400, 12), (20300, 14), (20200, 16))):       # three shootout attempts
                f = os.path.join(d, f"20261114T13{i}000Z_b{i}")
                make_session(f, 13, ai)
                p = Path(f) / "parsed.jsonl"
                p.write_text(p.read_text().replace('"rearWing": 12}', f'"rearWing": {wing}}}', 1))
            session_report.run(os.path.join(d, "20261114T100000Z_aa"), out, store)
            res = None
            for i in range(3):
                res, _ = session_report.run(os.path.join(d, f"20261114T13{i}000Z_b{i}"), out, store)
            self.assertEqual((res["attempts"]["number"], res["attempts"]["of"]), (3, 3))
            self.assertEqual(res["attempts"]["list"][1]["setup_changes"], {"rearWing": {"before": 12, "after": 14}})
            self.assertEqual(res["attempts"]["list"][2]["setup_changes"], {"rearWing": {"before": 14, "after": 16}})
            self.assertEqual(res["since_previous"]["previous"], "Practice 1")      # not attempt 2
            self.assertEqual(res["since_previous"]["setup_changes"], {"rearWing": {"then": 12, "now": 16}})

    def test_race_report_compares_with_practice_not_qualifying(self):
        with tempfile.TemporaryDirectory() as d:
            store, out = Path(d) / "pace.json", Path(d) / "reports"
            make_session(os.path.join(d, "20261114T100000Z_p1"), 1, 20500)
            make_session(os.path.join(d, "20261114T120000Z_q1"), 8, 20100)
            make_session(os.path.join(d, "20261114T140000Z_r1"), 15, 20300)
            for name in ("p1", "q1"):
                session_report.run(os.path.join(d, f"2026111{'4T100000Z' if name == 'p1' else '4T120000Z'}_{name}"), out, store)
            res, _ = session_report.run(os.path.join(d, "20261114T140000Z_r1"), out, store)
            self.assertEqual(res["since_previous"]["previous"], "Practice 1")
            self.assertEqual([e["session_type"] for e in res["pace"]["weekend"]], [1, 15])
            # a qualifying report still sees the whole weekend
            resq, _ = session_report.run(os.path.join(d, "20261114T120000Z_q1"), out, store)
            self.assertEqual(sorted(e["session_type"] for e in resq["pace"]["weekend"]), [1, 8, 15])

    def test_session_without_a_lap_is_not_an_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            store, out = Path(d) / "pace.json", Path(d) / "reports"
            store.write_text(json.dumps([{"folder": "20261114T130000Z_aa", "weekend_id": 77, "track_id": 0,
                                          "session_type": 15, "conditions": "dry", "ai_best_ms": None,
                                          "ai_top5_median_ms": None, "my_best_ms": None}]))
            make_session(os.path.join(d, "20261114T140000Z_bb"), 15, 20500)
            res, _ = session_report.run(os.path.join(d, "20261114T140000Z_bb"), out, store)
            self.assertEqual((res["attempts"]["number"], res["attempts"]["of"]), (1, 1))

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
