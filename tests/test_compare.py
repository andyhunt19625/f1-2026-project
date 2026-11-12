import json
import os
import tempfile
import unittest

from race_engineer.compare import analyse, load_all, report

LENGTH = 1000
# Track: straight 0-300, SLOW corner 300-400 (steer 0.3), straight 400-600,
# FAST corner 600-700 (steer 0.1), straight 700-1000.


def steer_at(d):
    if 300 <= d < 400:
        return 0.3
    if 600 <= d < 700:
        return 0.1
    return 0.0


def speed_at(d, slot):
    if 300 <= d < 400:
        return {0: 90, 1: 100, 2: 80}[slot]
    if 600 <= d < 700:
        return 230
    return 300


def t_at(d, slot):
    """Seconds into the lap. Everyone runs 50 m/s except in the slow corner:
    you (slot 0) 25 m/s, the faster car (slot 1) 50 m/s, the slower car (slot 2) 20 m/s."""
    v = {0: 25.0, 1: 50.0, 2: 20.0}[slot]
    if d <= 300:
        return d / 50.0
    if d <= 400:
        return 6.0 + (d - 300) / v
    return 6.0 + 100 / v + (d - 400) / 50.0


def build(path):
    lines = [{"packet_name": "Participants", "frameIdentifier": 0, "numActiveCars": 3, "participants": [
                {"aiControlled": 0, "name": "NORRIS"}, {"aiControlled": 1, "name": "VERSTAPPEN"},
                {"aiControlled": 1, "name": "STROLL"}]},
             {"packet_name": "Session", "frameIdentifier": 0, "trackLength": LENGTH},
             {"packet_name": "CarSetups", "frameIdentifier": 0, "carSetupData": [
                 {"frontWing": 20, "rearWing": 15}, {"frontWing": 25, "rearWing": 15},
                 {"frontWing": 20, "rearWing": 15}]}]
    frame = 1
    last = {0: 0, 1: 0, 2: 0}
    for lap in (1, 2, 3):
        for d in range(0, LENGTH if lap < 3 else 200, 10):
            lds, tels = [], []
            for slot in range(3):
                lds.append({"lapDistance": float(d), "currentLapTimeInMS": int(t_at(d, slot) * 1000),
                            "currentLapNum": lap, "currentLapInvalid": 0, "pitStatus": 1 if lap == 1 else 0,
                            "lastLapTimeInMS": last[slot]})
                in_slow = 300 <= d < 400
                tels.append({"speed": speed_at(d, slot), "throttle": 0.4 if in_slow else 1.0,
                             "brake": 0.9 if (d == 300 and slot == 0) or (d == 320 and slot == 1) else 0.0,
                             "gear": 3 if in_slow else 8, "steer": steer_at(d)})
            lines.append({"packet_name": "LapData", "frameIdentifier": frame, "lapData": lds})
            lines.append({"packet_name": "CarTelemetry", "frameIdentifier": frame, "carTelemetryData": tels})
            frame += 1
        for slot in range(3):
            last[slot] = int(t_at(LENGTH, slot) * 1000)
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(x) for x in lines) + "\n")


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "parsed.jsonl")
        build(self.path)
        self.res = analyse(load_all(self.path))

    def tearDown(self):
        self.dir.cleanup()

    def test_only_faster_cars_are_compared(self):
        self.assertEqual(self.res["you"], "NORRIS")
        self.assertEqual([r["driver"] for r in self.res["versus"]], ["VERSTAPPEN"])
        self.assertEqual(self.res["versus"][0]["gap_ms"], 2000)

    def test_loss_is_all_in_slow_corners(self):
        avg = self.res["average_loss_by_type_ms"]
        self.assertAlmostEqual(avg["slow"], 2000, delta=60)
        for k in ("medium", "fast", "straight"):
            self.assertLess(abs(avg.get(k, 0)), 60, k)

    def test_corners_found_and_typed(self):
        corners = self.res["versus"][0]["corners"]
        self.assertEqual([(c["from_m"], c["to_m"], c["type"]) for c in corners],
                         [(300, 400, "slow"), (600, 700, "fast")])
        slow = corners[0]
        self.assertAlmostEqual(slow["loss_ms"], 2000, delta=60)
        self.assertEqual(slow["you"]["apex_speed"], 90)
        self.assertEqual(slow["them"]["apex_speed"], 100)
        self.assertEqual(slow["you"]["brake_at_m"], 300)
        self.assertEqual(slow["them"]["brake_at_m"], 320)

    def test_setup_differences(self):
        self.assertEqual(self.res["versus"][0]["setup_differences"],
                         {"frontWing": {"you": 20, "them": 25}})

    def test_report_labels_guidance_as_general(self):
        text = report(self.res)
        self.assertIn("Biggest average loss is in slow sections", text)
        self.assertIn("NOT measured from your data", text)
        self.assertIn("frontWing 20 vs 25", text)

    def test_when_nobody_is_faster(self):
        data = load_all(self.path)
        data["laps"][1] = {}          # remove the faster car
        res = analyse(data)
        self.assertIn("No car was faster", res["note"])
        self.assertEqual(res["versus"][0]["driver"], "STROLL")


if __name__ == "__main__":
    unittest.main()
