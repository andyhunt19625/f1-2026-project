import json
import os
import tempfile
import unittest

from race_engineer import fuel_plan


def build(folder, weather=0, burn=1.5, laps=5, sc_lap=None):
    """Your car only. Fuel starts at 20 kg and drops `burn` kg per lap, spread over 10 frames.
    Lap 1 is a pit out-lap. If sc_lap is set, the Session packet shows a safety car during it."""
    os.makedirs(folder, exist_ok=True)
    lines = [{"packet_name": "Participants", "numActiveCars": 1, "participants": [{"aiControlled": 0, "name": "NORRIS"}]},
             {"packet_name": "Session", "trackId": 0, "sessionType": 1, "weather": weather, "totalLaps": 3,
              "safetyCarStatus": 0}]
    fuel, frame = 20.0, 1
    for lap in range(1, laps + 2):
        for k in range(10):
            if sc_lap == lap and k == 5:
                lines.append({"packet_name": "Session", "trackId": 0, "sessionType": 1, "weather": weather,
                              "safetyCarStatus": 1})
            if sc_lap is not None and lap == sc_lap + 1 and k == 0:
                lines.append({"packet_name": "Session", "trackId": 0, "sessionType": 1, "weather": weather,
                              "safetyCarStatus": 0})
            ld = {"currentLapNum": lap, "lastLapTimeInMS": 80000 if lap > 1 else 0, "pitStatus": 1 if lap == 1 else 0,
                  "pitLaneTimerActive": 0, "currentLapInvalid": 0}
            st = {"fuelInTank": round(fuel, 3), "fuelMix": 1}
            lines.append({"packet_name": "LapData", "frameIdentifier": frame, "lapData": [ld]})
            lines.append({"packet_name": "CarStatus", "frameIdentifier": frame, "carStatusData": [st]})
            frame += 1
            fuel -= burn / 10
    with open(os.path.join(folder, "parsed.jsonl"), "w") as f:
        f.write("\n".join(json.dumps(x) for x in lines) + "\n")


class FuelPlanTests(unittest.TestCase):
    def test_dry_and_wet_burn_and_plan(self):
        with tempfile.TemporaryDirectory() as d:
            build(os.path.join(d, "dry"), weather=0, burn=1.5)
            build(os.path.join(d, "wet"), weather=3, burn=1.2)
            res = fuel_plan.plan([os.path.join(d, "dry"), os.path.join(d, "wet")], race_laps=20)
            self.assertAlmostEqual(res["dry"]["burn_kg_per_lap"], 1.5, places=3)
            self.assertAlmostEqual(res["wet"]["burn_kg_per_lap"], 1.2, places=3)
            self.assertEqual(res["dry"]["laps"], 4)              # 5 laps measured, lap 1 was a pit out-lap
            self.assertAlmostEqual(res["dry"]["start_fuel_kg"], 1.5 * 22, places=1)   # 20 + formation + margin
            self.assertEqual(res["tracks"], [0])
            self.assertIn("my assumptions", fuel_plan.report(res))

    def test_safety_car_lap_left_out(self):
        with tempfile.TemporaryDirectory() as d:
            build(os.path.join(d, "s"), burn=1.5, sc_lap=3)
            res = fuel_plan.plan([os.path.join(d, "s")], race_laps=10)
            self.assertNotIn(3, [r["lap"] for r in res["laps_used"]])
            self.assertEqual(res["dry"]["laps"], 3)


class DigestTests(unittest.TestCase):
    def test_burn_from_digest_skips_pit_safety_car_and_applies_weather(self):
        laps = {}
        fuel = 20.0
        for n in range(1, 8):
            laps[(1, n, 0)] = {"fuel_start": fuel, "time_ms": 90000, "pit": n == 1, "valid": True, "mix": 1}
            fuel -= 0.5 if n in (4, 5) else 1.4          # laps 4-5 under the safety car
        laps[(1, 8, 0)] = {"fuel_start": fuel, "time_ms": None, "pit": False, "valid": True, "mix": 1}
        d = {"meta": {"player": 0, "track_id": 2, "session_type": 1, "total_laps": 5},
             "laps": [laps], "weather": [(0.0, 0, 20, 25)], "safety_car": [(100.0, 1, 4), (200.0, 0, 5)]}
        track, stype, total, rows = fuel_plan.laps_from_digest(d)
        self.assertEqual((track, stype), (2, 1))
        used = [r["lap"] for r in rows if fuel_plan.usable(r)]
        self.assertEqual(used, [2, 3, 6, 7])             # lap 1 pit, laps 4-5 safety car
        import gzip, pickle, tempfile as tf
        with tf.TemporaryDirectory() as t:
            p = os.path.join(t, "digest.pkl.gz")
            with gzip.open(p, "wb") as f:
                pickle.dump(d, f)
            res = fuel_plan.plan([], 20, digests=[p])
            self.assertAlmostEqual(res["dry"]["burn_kg_per_lap"], 1.4, places=3)
            self.assertAlmostEqual(res["dry"]["start_fuel_kg"], 1.4 * 22, places=1)
            self.assertIsNone(res["wet"])


if __name__ == "__main__":
    unittest.main()
