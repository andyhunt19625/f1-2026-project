import json
import os
import tempfile
import unittest
from pathlib import Path

from race_engineer import session_digest, session_report
from tests.test_live_digest import drive
from tests.test_session_report import make_session


def add_damage(folder):
    """Insert a CarDamage packet before every LapData packet: your tyres at 2 x lap number %, the AI's at 1 x."""
    p = Path(folder) / "parsed.jsonl"
    out = []
    for line in p.read_text().splitlines():
        d = json.loads(line)
        if d["packet_name"] == "LapData":
            lap = d["lapData"][0]["currentLapNum"]
            out.append({"packet_name": "CarDamage", "frameIdentifier": d["frameIdentifier"], "carDamageData": [
                {"tyresWear": [2.0 * lap] * 4}, {"tyresWear": [1.0 * lap] * 4}]})
        out.append(d)
    p.write_text("\n".join(json.dumps(x) for x in out) + "\n")


def entry(lap, wear, age, compound=17):
    return {"lap": lap, "wear": wear, "age": age, "compound": compound}


class TyreSummaryTests(unittest.TestCase):
    def digest(self):
        mine = [entry(i, [2.0 * i + 1, 2.0 * i + 1, 3.0 * i + 1, 3.0 * i + 1], i) for i in range(0, 6)]   # fronts wear faster
        ai = [entry(i, [1.5 * i] * 4, i) for i in range(0, 6)]
        pit = [entry(0, [0, 0, 0, 0], 0), entry(1, [1, 1, 1, 1], 1), entry(2, [2, 2, 2, 2], 2)]
        return {"meta": {"player": 0}, "tyre_log": {0: mine, 1: ai, 2: ai, 3: pit}}

    def test_rates_projection_and_ai(self):
        t = session_report.tyre_summary(self.digest())
        self.assertEqual(t["wheels"][2], "front-left")
        self.assertEqual(t["stint_rate_per_lap"], [2.0, 2.0, 3.0, 3.0])
        self.assertEqual(t["stint_laps"], 6)
        # at lap 5: rear 11 %, front 16 %; 30 % at +9.5 laps (rear), +4.7 laps (front)
        self.assertEqual(t["laps_to_limit"], [9.5, 9.5, 4.7, 4.7])
        self.assertEqual(t["ai"]["cars"], 3)
        self.assertEqual(t["ai"]["median_rate_per_lap"], 1.5)
        self.assertEqual(t["laps"][1]["compound"], "medium")

    def test_only_the_current_stint_counts_after_a_pit_stop(self):
        mine = [entry(i, [4.0 * i] * 4, i) for i in range(0, 4)] + [entry(4, [0.0] * 4, 0, 18), entry(5, [1.0] * 4, 1, 18),
                                                                    entry(6, [2.0] * 4, 2, 18)]
        t = session_report.tyre_summary({"meta": {"player": 0}, "tyre_log": {0: mine}})
        self.assertEqual(t["stint_laps"], 3)
        self.assertEqual(t["stint_rate_per_lap"], [1.0, 1.0, 1.0, 1.0])
        self.assertEqual(t["laps_to_limit"], [28.0] * 4)

    def test_not_enough_data_gives_none(self):
        self.assertIsNone(session_report.tyre_summary({"meta": {"player": 0}, "tyre_log": {0: [entry(0, [0] * 4, 0)]}}))
        self.assertIsNone(session_report.tyre_summary({"meta": {"player": 0}}))


class TyreLogTests(unittest.TestCase):
    def check(self, log):
        mine = log[0]
        self.assertEqual([e["lap"] for e in mine], [0, 1, 2])
        self.assertEqual([e["wear"][0] for e in mine], [2.0, 4.0, 6.0])
        self.assertEqual([e["wear"][0] for e in log[1]], [1.0, 2.0, 3.0])     # the AI car too
        self.assertEqual(len(mine[0]["wear"]), 4)

    def test_file_digest_and_live_digest_record_the_same_wear(self):
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "20261114T100000Z_aa")
            make_session(folder, 1, 20500)
            add_damage(folder)
            self.check(session_digest.build(Path(folder) / "parsed.jsonl")["tyre_log"])
            state = drive(folder, Path(d) / "live")
            self.check(state.live_laps.digest()["tyre_log"])


if __name__ == "__main__":
    unittest.main()
