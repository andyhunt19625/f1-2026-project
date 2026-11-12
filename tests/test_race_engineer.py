import json
import os
import tempfile
import unittest

from race_engineer.replay import replay
from race_engineer.state import LiveState
from race_engineer.tools import ToolError, find_driver, gap_to, pit_window, tyre_status

NAMES = ["NORRIS", "PIASTRI", "HAMILTON", "PÉREZ"]  # synthetic order; slots differ per session


def participants():
    return {"packet_name": "Participants", "sessionTime": 0.0, "numActiveCars": 4,
            "participants": [{"aiControlled": 0 if i == 0 else 1, "name": n, "raceNumber": 10 + i}
                             for i, n in enumerate(NAMES)]}


def lap(pos, lapnum, leader_ms, front_ms, stops=0, pit_active=0, lane_ms=0):
    return {"carPosition": pos, "currentLapNum": lapnum, "numPitStops": stops,
            "deltaToRaceLeaderMSPart": leader_ms % 60000, "deltaToRaceLeaderMinutesPart": leader_ms // 60000,
            "deltaToCarInFrontMSPart": front_ms % 60000, "deltaToCarInFrontMinutesPart": front_ms // 60000,
            "pitLaneTimerActive": pit_active, "pitLaneTimeInLaneInMS": lane_ms}


def lapdata(laps, st=0.0):
    return {"packet_name": "LapData", "sessionTime": st, "lapData": laps}


def damage(wears, st=0.0):
    return {"packet_name": "CarDamage", "sessionTime": st,
            "carDamageData": [{"tyresWear": w} for w in wears]}


class Tests(unittest.TestCase):
    def setUp(self):
        self.s = LiveState()
        self.s.ingest(participants())

    def test_find_driver_by_name_and_accent(self):
        self.assertEqual(find_driver(self.s, "Lewis Hamilton"), 2)
        self.assertEqual(find_driver(self.s, "perez"), 3)
        with self.assertRaises(ToolError):
            find_driver(self.s, "Alonso")

    def test_player_from_ai_flag(self):
        self.assertEqual(self.s.player_slot(), 0)

    def test_gap_minutes_part(self):
        # me P2 at 65.0 s from leader (1 min 5 s), Hamilton P3 at 70.5 s
        self.s.ingest(lapdata([lap(2, 5, 65000, 5000), lap(1, 5, 0, 0),
                               lap(3, 5, 70500, 5500), lap(4, 5, 80000, 9500)]))
        r = gap_to(self.s, "Hamilton")
        self.assertAlmostEqual(r["gap_s"], 5.5)
        self.assertTrue(r["other_is_behind"])
        self.assertIn("5.5 seconds behind", r["answer"])
        r = gap_to(self.s, "Piastri")
        self.assertAlmostEqual(r["gap_s"], 65.0)
        self.assertFalse(r["other_is_behind"])

    def test_pit_window_brief_example(self):
        # 26 s pit loss, 30 s gap, 5 s buffer -> 4 s clear, not enough; needs 31
        self.s.ingest(lapdata([lap(1, 5, 0, 0), lap(2, 5, 30000, 30000),
                               lap(3, 5, 40000, 10000), lap(4, 5, 50000, 10000)]))
        r = pit_window(self.s, 26.0, 5.0)
        self.assertFalse(r["in_window"])
        self.assertAlmostEqual(r["rejoin_clear_s"], 4.0)
        self.assertAlmostEqual(r["gap_needed_s"], 31.0)
        self.assertTrue(pit_window(self.s, 26.0, 2.0)["in_window"])
        with self.assertRaises(ToolError):
            pit_window(self.s, 26.0, 6.0)
        with self.assertRaises(ToolError):
            pit_window(self.s, None)

    def test_tyre_rate_and_threshold(self):
        w = lambda a: [[a, a - 2, a - 8, a - 5]] + [[0, 0, 0, 0]] * 3
        laps = lambda n: lapdata([lap(1, n, 0, 0), lap(2, n, 1, 1), lap(3, n, 2, 1), lap(4, n, 3, 1)])
        for n, a in ((2, 4), (3, 8), (4, 12), (5, 16)):  # 4% a lap
            self.s.ingest(damage(w(a)))
            self.s.ingest(laps(n))
        r = tyre_status(self.s)
        self.assertEqual(r["worst_pct"], 16)
        self.assertAlmostEqual(r["rate_pct_per_lap"], 4.0)
        self.assertAlmostEqual(r["laps_to_degrade"], 3.5)
        self.s.ingest(damage(w(31)))
        self.assertEqual(tyre_status(self.s)["laps_to_degrade"], 0.0)

    def test_pit_resets_stint_history(self):
        self.s.ingest(damage([[10, 10, 10, 10]] + [[0] * 4] * 3))
        self.s.ingest(lapdata([lap(1, 5, 0, 0, 0)] * 4))
        self.s.ingest(lapdata([lap(1, 6, 0, 0, 1)] * 4))
        # lap-5 sample was dropped at the stop; only the new stint's lap-6 sample remains
        self.assertEqual([h["laps_completed"] for h in self.s.wear_history[0]], [5])

    def test_pit_lane_time(self):
        self.s.ingest(lapdata([lap(1, 5, 0, 0, 0, 1, 12000)] * 4))
        self.s.ingest(lapdata([lap(1, 5, 0, 0, 1, 1, 19016)] * 4))
        self.s.ingest(lapdata([lap(1, 5, 0, 0, 1, 0, 0)] * 4))
        self.assertEqual(self.s.pit_lane[0]["completed_ms"], [19016])

    def test_replay_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "parsed.jsonl")
            lines = [{"t": 1.0, "packet_name": "Motion", "packet_id": 0, "sessionTime": 0.0},
                     dict(participants(), t=1.1, packet_id=4),
                     dict(lapdata([lap(1, 1, 0, 0)] * 4, 5.0), t=1.2),
                     dict(lapdata([lap(1, 2, 0, 0)] * 4, 99.0), t=1.3)]
            with open(p, "w") as f:
                f.write("\n".join(json.dumps(x) for x in lines) + "\n")
            s = replay(p, LiveState(), until_session_time=50)
            self.assertEqual(s.counts["LapData"], 1)
            self.assertEqual(s.counts.get("Motion", 0), 0)


if __name__ == "__main__":
    unittest.main()
