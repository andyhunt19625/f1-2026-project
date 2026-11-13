import json
import tempfile
import unittest
from pathlib import Path

from race_engineer import pace_model


def e(folder, wk, tr, typ, med, best, cond="dry"):
    return {"folder": folder, "weekend_id": wk, "track_id": tr, "session_type": typ, "conditions": cond,
            "ai_median_ms": med, "ai_best_ms": best, "ai_n": 21, "my_best_ms": best}


def weekend(wk, tr, day, p1, q_factor=None, steps=0.01):
    """Practice P1 med/best, then 2 more practices each `steps` quicker, then qualifying q_factor quicker than P3."""
    out, med, best = [], p1[0], p1[1]
    for i, typ in enumerate((1, 2, 3)):
        out.append(e(f"{day}T1{i}0000Z_x", wk, tr, typ, round(med), round(best)))
        med, best = med * (1 - steps), best * (1 - steps)
    if q_factor is not None:
        pm, pb = out[-1]["ai_median_ms"], out[-1]["ai_best_ms"]
        out.append(e(f"{day}T150000Z_x", wk, tr, 8, round(pm * (1 - q_factor)), round(pb * (1 - q_factor))))
    return out


class PaceModelTests(unittest.TestCase):
    def test_same_percent_rule_when_there_is_no_history(self):
        store = weekend(1, 5, "20261114", (100000, 98000))[:3]
        entry = store[-1]
        m = pace_model.build(store, entry)
        self.assertEqual([round(r["step"]["median_pct"], 2) for r in m["practice"] if r["step"]], [1.0, 1.0])
        self.assertEqual(m["headline"]["level"], 3)
        self.assertEqual(m["headline"]["median_ms"], round(98010 * 0.99))
        self.assertAlmostEqual(m["headline"]["best_ms"], 96050 * 0.99, delta=5)   # best: 98000 -> 97020 -> 96050
        self.assertIsNone(m["actual"])

    def test_needs_two_practices_or_history(self):
        store = weekend(1, 5, "20261114", (100000, 98000))[:1]
        m = pace_model.build(store, store[0])
        self.assertIsNone(m["headline"])
        self.assertIn("Needs two practice", m["note"])

    def test_after_qualifying_actual_error_and_saved_gain(self):
        store = weekend(1, 5, "20261114", (100000, 98000), q_factor=0.02)
        entry = store[-1]
        m = pace_model.build(store, entry)
        a = m["actual"]
        self.assertEqual(a["quali_type"], 8)
        self.assertAlmostEqual(a["median_pct"], 2.0, places=2)
        self.assertEqual(m["levels"][0]["level"], 3)                       # nothing else to learn from yet
        err = a["errors"][0]
        self.assertEqual(err["median_error_ms"], m["levels"][0]["median_ms"] - a["median_ms"])
        self.assertGreater(err["median_error_ms"], 0)                      # predicted 1 % gain, got 2 %: model too slow
        self.assertIn("saved for this circuit", a["saved"])
        self.assertEqual(len(pace_model.all_gains(store)), 1)

    def test_circuit_history_beats_the_practice_rule_and_global_needs_three_weekends(self):
        past = (weekend(1, 5, "20261101", (100000, 98000), q_factor=0.02)
                + weekend(2, 6, "20261108", (90000, 88000), q_factor=0.03))
        # new weekend at track 5 (seen before): level 1 uses the saved 2 %
        new = weekend(3, 5, "20261115", (100000, 98000))
        store = past + new
        lv = pace_model.build(store, new[-1])["levels"]
        self.assertEqual([l["level"] for l in lv], [1, 3])
        self.assertAlmostEqual(lv[0]["median_gain_pct"], 2.0, places=1)
        # new weekend at a new track with 3 saved weekends: global level appears
        more = weekend(4, 7, "20261122", (80000, 78000), q_factor=0.04)
        store = past + more + weekend(5, 9, "20261129", (70000, 68000))
        lv = pace_model.build(store, store[-1])["levels"]
        self.assertEqual([l["level"] for l in lv], [2, 3])
        self.assertAlmostEqual(lv[0]["median_gain_pct"], 3.0, places=1)    # median of 2, 3, 4 %

    def test_wet_and_dry_are_kept_apart_and_restarts_use_the_quickest_attempt(self):
        store = weekend(1, 5, "20261114", (100000, 98000), q_factor=0.02)
        store.append(e("20261114T160000Z_y", 1, 5, 8, 90000, 88000, cond="wet"))      # a wet quali: not comparable
        self.assertEqual(len(pace_model.all_gains(store)), 1)
        slow = e("20261114T111500Z_z", 1, 5, 2, 99500, 99000)                         # a slower restart of practice 2
        v = pace_model.view(store + [slow], 1, 5)[0]
        self.assertEqual(len(v), 3)
        self.assertNotIn("20261114T111500Z_z", [p["folder"] for p in v])

    def test_save_writes_the_gains(self):
        store = weekend(1, 5, "20261114", (100000, 98000), q_factor=0.02)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pace_model.json"
            pace_model.save(store, p)
            data = json.loads(p.read_text())
            self.assertEqual(len(data["saved_real_gains"]), 1)
            self.assertEqual(data["saved_real_gains"][0]["track_id"], 5)


if __name__ == "__main__":
    unittest.main()
