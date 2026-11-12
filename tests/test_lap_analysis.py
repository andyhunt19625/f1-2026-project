import json
import os
import tempfile
import unittest

from race_engineer.lap_analysis import analyse, load, report

LENGTH = 1000


def build_session(path):
    """Lap 1: pit out-lap. Lap 2: 50 m/s everywhere (best). Lap 3: 25 m/s from 400-500 m
    (2 s lost there, braking earlier), otherwise identical. Lap 4: invalid. Lap 5 is the
    unfinished current lap."""
    lines = [{"packet_name": "Participants", "frameIdentifier": 0, "numActiveCars": 2,
              "participants": [{"aiControlled": 1, "name": "PIASTRI"}, {"aiControlled": 0, "name": "NORRIS"}]},
             {"packet_name": "Session", "frameIdentifier": 0, "trackLength": LENGTH}]
    frame = 1
    last_lap_ms = 0

    def lap_time_to(d, slow):
        if not slow or d <= 400:
            return d / 50.0
        if d <= 500:
            return 8.0 + (d - 400) / 25.0
        return 12.0 + (d - 500) / 50.0

    for lap in (1, 2, 3, 4, 5):
        slow = lap == 3
        dists = range(0, LENGTH if lap != 5 else 300, 10)
        for d in dists:
            t_ms = int(lap_time_to(d, slow) * 1000)
            ld = {"lapDistance": float(d), "currentLapTimeInMS": t_ms, "currentLapNum": lap,
                  "currentLapInvalid": 1 if lap == 4 else 0, "pitStatus": 1 if lap == 1 else 0,
                  "lastLapTimeInMS": last_lap_ms}
            in_slow = slow and 400 <= d < 500
            tel = {"speed": 90 if in_slow else 180, "throttle": 0.3 if in_slow else 1.0,
                   "brake": 0.8 if (slow and d == 400) else 0.0, "gear": 4 if in_slow else 7}
            other = dict(ld, lapDistance=0.0)
            lines.append({"packet_name": "LapData", "frameIdentifier": frame, "lapData": [other, ld]})
            lines.append({"packet_name": "CarTelemetry", "frameIdentifier": frame,
                          "carTelemetryData": [tel, tel]})
            frame += 1
        last_lap_ms = int(lap_time_to(LENGTH, slow) * 1000)
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(x) for x in lines) + "\n")


class LapAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "parsed.jsonl")
        build_session(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def test_finds_driver_and_excludes_pit_invalid_and_unfinished_laps(self):
        laps, length, name = load(self.path)
        self.assertEqual(name, "NORRIS")
        self.assertEqual(length, LENGTH)
        res = analyse(laps, length, 100)
        self.assertEqual(res["clean_laps"], [2, 3])
        self.assertEqual(res["best_lap"], 2)
        self.assertEqual(res["best_time_ms"], 20000)

    def test_time_loss_located_in_the_right_segment(self):
        laps, length, _ = load(self.path)
        res = analyse(laps, length, 100)
        lap3 = res["per_lap"][3]
        self.assertEqual(lap3["gap_to_best_ms"], 2000)
        worst = max(lap3["segments"], key=lambda s: s["loss_ms"])
        self.assertEqual((worst["from_m"], worst["to_m"]), (400, 500))
        self.assertAlmostEqual(worst["loss_ms"], 2000, delta=50)
        others = sum(s["loss_ms"] for s in lap3["segments"] if s is not worst)
        self.assertLess(abs(others), 60)
        self.assertEqual(worst["you"]["min_speed"], 90)
        self.assertEqual(worst["best"]["min_speed"], 180)
        self.assertEqual(worst["you"]["brake_at_m"], 400)
        self.assertEqual(res["average_loss_by_segment"][0]["from_m"], 400)

    def test_report_text(self):
        laps, length, _ = load(self.path)
        text = report(analyse(laps, length, 100))
        self.assertIn("Best clean lap: lap 2 in 0:20.000", text)
        self.assertIn("400-500", text)

    def test_needs_two_clean_laps(self):
        laps, length, _ = load(self.path)
        del laps[3]
        res = analyse(laps, length, 100)
        self.assertIn("error", res)
        self.assertIn("lap 4", report(res))


if __name__ == "__main__":
    unittest.main()
