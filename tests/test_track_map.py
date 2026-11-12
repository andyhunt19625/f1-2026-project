import json
import math
import os
import tempfile
import unittest

from race_engineer import track_map

LENGTH = 1000


def build_file(path):
    """Circle track, 1000 m. Lap 1: 20 s flat. Lap 2: 19 s, all the gain in 0-100 m."""
    lines = [{"packet_name": "Participants", "frameIdentifier": 0, "numActiveCars": 1,
              "participants": [{"aiControlled": 0, "name": "NORRIS"}]},
             {"packet_name": "Session", "frameIdentifier": 0, "trackLength": LENGTH}]
    r = LENGTH / (2 * math.pi)
    frame = 1
    for lap in (1, 2, 3):
        for d in range(0, LENGTH if lap < 3 else 50, 10):
            t = d * 20 if lap != 2 else (d * 10 if d <= 100 else 1000 + (d - 100) * 20)
            ang = d / LENGTH * 2 * math.pi
            ld = {"lapDistance": float(d), "currentLapTimeInMS": t, "currentLapNum": lap, "currentLapInvalid": 0,
                  "pitStatus": 0, "driverStatus": 1, "lastLapTimeInMS": {1: 0, 2: 20000, 3: 19000}[lap]}
            tel = {"speed": 200, "throttle": 1.0, "brake": 0.0, "gear": 7, "steer": 0.1}
            mo = {"worldPositionX": r * math.cos(ang), "worldPositionY": 0.0, "worldPositionZ": r * math.sin(ang)}
            for name, key, val in (("Motion", "carMotionData", mo), ("LapData", "lapData", ld),
                                   ("CarTelemetry", "carTelemetryData", tel)):
                lines.append({"packet_name": name, "frameIdentifier": frame, key: [val]})
            frame += 1
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(x) for x in lines) + "\n")


class TrackMapTests(unittest.TestCase):
    def test_map_points_and_gains(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "parsed.jsonl")
            build_file(p)
            laps, length = track_map.load(p)
            res = track_map.build(laps, length, "lap 2", "lap 1")
            self.assertEqual(res["lap_ms"], 19000)
            self.assertEqual(res["ref_ms"], 20000)
            self.assertGreater(len(res["points"]), 90)
            first = res["segments"][0]
            self.assertEqual((first["from_m"], first["to_m"]), (0, 100))
            self.assertAlmostEqual(first["gain_ms"], 1000, delta=30)
            self.assertAlmostEqual(sum(s["gain_ms"] for s in res["segments"]), 1000, delta=30)
            x, z = res["points"][0][1], res["points"][0][2]
            self.assertAlmostEqual(math.hypot(x, z), LENGTH / (2 * math.pi), delta=0.1)

    def test_unknown_lap_lists_available(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "parsed.jsonl")
            build_file(p)
            laps, length = track_map.load(p)
            with self.assertRaises(SystemExit) as e:
                track_map.build(laps, length, "lap 9", "lap 1")
            self.assertIn("lap 1", str(e.exception))


if __name__ == "__main__":
    unittest.main()
