import json
import os
import tempfile
import unittest

from race_engineer import ers_analysis

LENGTH = 1000


def build(path):
    """Two cars, 1000 m lap: 0-500 m straight (steer 0, 250 km/h), 500-1000 m slow corner
    (steer 0.3, 100 km/h). Lap time 20 s, both cars, 10 m samples.
    You (slot 0): MGU-K 100 kW on the straight, store 4.0 -> 3.0 MJ there, flat in the corner.
    AI (slot 1): MGU-K 100 kW in the corner, store 4.0 -> 3.5 MJ there, flat on the straight."""
    lines = [{"packet_name": "Participants", "frameIdentifier": 0, "numActiveCars": 2,
              "participants": [{"aiControlled": 0, "name": "NORRIS"}, {"aiControlled": 1, "name": "SAINZ"}]},
             {"packet_name": "Session", "frameIdentifier": 0, "trackLength": LENGTH}]
    frame = 1
    for lap in (1, 2, 3):
        for d in range(0, LENGTH if lap < 3 else 100, 10):
            straight = d < 500
            t_ms = d * 20
            lds, tels, sts = [], [], []
            for car in (0, 1):
                lds.append({"lapDistance": float(d), "currentLapTimeInMS": t_ms, "currentLapNum": lap,
                            "currentLapInvalid": 0, "pitStatus": 0, "driverStatus": 1,
                            "lastLapTimeInMS": 20000 if lap > 1 else 0})
                tels.append({"speed": 250 if straight else 100, "throttle": 1.0, "brake": 0.0, "gear": 7,
                             "steer": 0.0 if straight else 0.3})
                if car == 0:
                    store = 4e6 - 1e6 * min(d, 500) / 500
                    mguk = 100000.0 if straight else 0.0
                else:
                    store = 4e6 - 0.5e6 * max(0, d - 500) / 500
                    mguk = 0.0 if straight else 100000.0
                sts.append({"ersStoreEnergy": store, "enginePowerMGUK": mguk, "ersDeployMode": 1,
                            "ersDeployedThisLap": 1e6, "ersHarvestedThisLapMGUK": 0.2e6,
                            "ersHarvestedThisLapMGUH": 0.0, "ersHarvestLimitPerLap": 6.5e6})
            for name, key, val in (("LapData", "lapData", lds), ("CarStatus", "carStatusData", sts),
                                   ("CarTelemetry", "carTelemetryData", tels)):
                lines.append({"packet_name": name, "frameIdentifier": frame, key: val})
            frame += 1
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(x) for x in lines) + "\n")


class ErsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        p = os.path.join(self.dir.name, "parsed.jsonl")
        build(p)
        self.res = ers_analysis.analyse(ers_analysis.load(p))

    def tearDown(self):
        self.dir.cleanup()

    def test_finds_laps_and_other_car(self):
        self.assertEqual(self.res["best_ms"], 20000)
        self.assertEqual(self.res["other"]["driver"], "SAINZ")
        self.assertEqual(sorted(self.res["laps"]), ["lap 1", "lap 2"])

    def test_energy_by_type(self):
        st, slow = self.res["by_type"]["straight"], self.res["by_type"]["slow"]
        self.assertAlmostEqual(st["you_net_MJ"], -1.0, delta=0.03)
        self.assertAlmostEqual(slow["you_net_MJ"], 0.0, delta=0.03)
        self.assertAlmostEqual(st["them_net_MJ"], 0.0, delta=0.03)
        self.assertAlmostEqual(slow["them_net_MJ"], -0.5, delta=0.03)
        # 100 kW for 10 s (500 m at 20 ms per metre) = 1.0 MJ
        self.assertAlmostEqual(st["you_mguk_MJ"], 1.0, delta=0.03)
        self.assertAlmostEqual(slow["them_mguk_MJ"], 1.0, delta=0.03)

    def test_lap_summary_and_sections(self):
        s = self.res["best_summary"]
        self.assertAlmostEqual(s["store_start_MJ"], 4.0)
        self.assertAlmostEqual(s["store_min_MJ"], 3.0, delta=0.01)
        self.assertEqual(s["deploy_mode_share"], {1: 100})
        self.assertEqual([(r["from_m"], r["to_m"], r["type"]) for r in self.res["sections"]],
                         [(0, 500, "straight"), (500, 1000, "slow")])
        text = ers_analysis.report(self.res)
        self.assertIn("Sainz", text)
        self.assertIn("straight", text)


if __name__ == "__main__":
    unittest.main()
