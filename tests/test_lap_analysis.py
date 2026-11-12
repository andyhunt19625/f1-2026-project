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
        self.assertEqual(res["clean_laps"], ["lap 2", "lap 3"])
        self.assertEqual(res["best_lap"], "lap 2")
        self.assertEqual(res["best_time_ms"], 20000)

    def test_time_loss_located_in_the_right_segment(self):
        laps, length, _ = load(self.path)
        res = analyse(laps, length, 100)
        lap3 = res["per_lap"]["lap 3"]
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
        del laps[(1, 3, 0)]
        res = analyse(laps, length, 100)
        self.assertIn("error", res)
        self.assertIn("lap 4", report(res))


def build_two_runs(path):
    """Run 1: lap 1 out-lap (pit), lap 2 clean 20 s, lap 3 in-lap (pit).
    Run 2 (back out of the garage): lap numbers AND frame numbers start again from 1.
    Run 2 lap 1 out-lap (pit), lap 2 clean 19 s (low fuel)."""
    lines = [{"packet_name": "Participants", "frameIdentifier": 0, "numActiveCars": 1,
              "participants": [{"aiControlled": 0, "name": "NORRIS"}]},
             {"packet_name": "Session", "frameIdentifier": 0, "trackLength": LENGTH}]

    def lap_frames(frame, lap, secs, pit, last_ms):
        out = []
        for d in range(0, LENGTH, 10):
            ld = {"lapDistance": float(d), "currentLapTimeInMS": int(d / LENGTH * secs * 1000),
                  "currentLapNum": lap, "currentLapInvalid": 0, "pitStatus": pit, "lastLapTimeInMS": last_ms}
            tel = {"speed": 200, "throttle": 1.0, "brake": 0.0, "gear": 7, "steer": 0.0}
            out.append({"packet_name": "LapData", "frameIdentifier": frame, "lapData": [ld]})
            out.append({"packet_name": "CarTelemetry", "frameIdentifier": frame, "carTelemetryData": [tel]})
            frame += 1
        return out, frame

    frame = 1
    for lap, secs, pit, last in ((1, 25, 1, 0), (2, 20, 0, 25000), (3, 24, 1, 20000)):
        chunk, frame = lap_frames(frame, lap, secs, pit, last)
        lines += chunk
    frame = 1                                      # frame counter starts again
    for lap, secs, pit, last in ((1, 25, 1, 24000), (2, 19, 0, 25000), (3, 24, 1, 19000)):
        chunk, frame = lap_frames(frame, lap, secs, pit, last)
        lines += chunk
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(x) for x in lines) + "\n")


class TwoRunTests(unittest.TestCase):
    def test_runs_are_kept_apart_and_frame_reset_does_not_mix_data(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "parsed.jsonl")
            build_two_runs(p)
            laps, length, _ = load(p)
            res = analyse(laps, length, 100)
            self.assertEqual(res["clean_laps"], ["lap 2", "run 2 lap 2"])
            self.assertEqual(res["best_lap"], "run 2 lap 2")
            self.assertEqual(res["best_time_ms"], 19000)
            self.assertEqual(res["per_lap"]["lap 2"]["gap_to_best_ms"], 1000)
            total = sum(s["loss_ms"] for s in res["per_lap"]["lap 2"]["segments"])
            self.assertAlmostEqual(total, 1000, delta=30)   # segment losses add up to the lap gap
            self.assertEqual(len(laps[(1, 2, 0)]["samples"]), LENGTH // 10)  # not mixed with run 2


def build_garage_then_flying(path):
    """Copies the pattern seen in Australia P1 (12 Nov 2026): lap 1 flying 20 s, then lap 2 starts,
    the car goes to the garage (driverStatus 0, pitStatus 1), comes out on an out-lap
    (driverStatus 3), and at the line the status turns to flying (1) and the timer restarts,
    STILL lap 2. That flying part takes 19 s, then lap 3 starts with lastLap = 19000."""
    lines = [{"packet_name": "Participants", "frameIdentifier": 0, "numActiveCars": 1,
              "participants": [{"aiControlled": 0, "name": "NORRIS"}]},
             {"packet_name": "Session", "frameIdentifier": 0, "trackLength": LENGTH}]
    frame = [1]

    def add(lap, dist, t_ms, status, pit, last):
        ld = {"lapDistance": float(dist), "currentLapTimeInMS": t_ms, "currentLapNum": lap,
              "currentLapInvalid": 0, "pitStatus": pit, "lastLapTimeInMS": last, "driverStatus": status}
        tel = {"speed": 200, "throttle": 1.0, "brake": 0.0, "gear": 7, "steer": 0.0}
        lines.append({"packet_name": "LapData", "frameIdentifier": frame[0], "lapData": [ld]})
        lines.append({"packet_name": "CarTelemetry", "frameIdentifier": frame[0], "carTelemetryData": [tel]})
        frame[0] += 1

    for d in range(0, LENGTH, 10):                       # lap 1 flying, 20 s
        add(1, d, int(d / LENGTH * 20000), 1, 0, 0)
    for d in range(0, 100, 10):                          # lap 2 starts flying ...
        add(2, d, int(d / LENGTH * 20000), 1, 0, 20000)
    for d in (5000, 5100, 5200):                         # ... then the garage
        add(2, d, 0, 0, 1, 20000)
    for d in range(600, LENGTH, 50):                     # out-lap from the pit exit
        add(2, d, (d - 600) * 30, 3, 0, 20000)
    for d in range(0, LENGTH, 10):                       # flying lap from the line, same lap number
        add(2, d, int(d / LENGTH * 19000), 1, 0, 20000)
    add(3, 1, 0, 1, 0, 19000)                            # lap 3 starts: lap 2 took 19.000 s
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(x) for x in lines) + "\n")


class GarageFlyingTests(unittest.TestCase):
    def test_flying_lap_after_garage_is_kept_with_its_own_time(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "parsed.jsonl")
            build_garage_then_flying(p)
            laps, length, _ = load(p)
            res = analyse(laps, length, 100)
            self.assertEqual(res["clean_laps"], ["lap 1", "lap 2 (from the line)"])
            self.assertEqual(res["best_lap"], "lap 2 (from the line)")
            self.assertEqual(res["best_time_ms"], 19000)
            self.assertTrue(res["all_laps"]["lap 2"]["pit"])          # the garage part is excluded
            self.assertIsNone(res["all_laps"]["lap 2"]["time_ms"])
            total = sum(s["loss_ms"] for s in res["per_lap"]["lap 1"]["segments"])
            self.assertAlmostEqual(total, 1000, delta=30)


if __name__ == "__main__":
    unittest.main()
