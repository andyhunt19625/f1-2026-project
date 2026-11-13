import unittest

from race_engineer import report_layers

LENGTH = 1000


def lap(brake_at=400, fuel_slope=1.0, wear_slope=1.0, kw=100.0, full_at=600, with_fuel=True, with_wear=True):
    """1000 m lap sampled every 10 m: brakes at brake_at for 60 m (speed 250 -> 90), full throttle from full_at,
    battery power kw on 0-500 m, fuel burns faster in 0-500 m, tyres wear in 400-700 m."""
    samples, ers_t, wear_s = [], [], []
    fuel = 20.0
    wear = 5.0
    for d in range(0, LENGTH, 10):
        braking = brake_at <= d < brake_at + 60
        speed = 250 - (d - brake_at) * 160 / 60 if braking else (250 if d < brake_at else 150 + (d - brake_at - 60) * 0.5)
        samples.append((float(d), d * 20, speed, 1.0 if d >= full_at else 0.2, 0.8 if braking else 0.0, 6, 0.0))
        burn = (0.004 if d < 500 else 0.002) * fuel_slope
        fuel -= burn * 10
        ers_t.append((float(d), d * 20, 4e6, kw * 1000 if d < 500 else 0.0, 0, fuel if with_fuel else None))
        wear += (0.03 if 400 <= d < 700 else 0.002) * wear_slope * 10 / 10
        wear_s.append((float(d), (wear,) * 4))
    out = {"samples": samples, "ers": ers_t, "time_ms": 20000, "valid": True, "pit": False}
    if with_wear:
        out["wear_s"] = wear_s
    return out


class LayerTests(unittest.TestCase):
    def test_battery_pair_segments(self):
        L = report_layers.battery(lap(kw=200.0), lap(kw=100.0), LENGTH)
        self.assertTrue(L["available"])
        first, last = L["segments"][0], L["segments"][-1]
        self.assertEqual((first["from_m"], first["to_m"], first["you"], first["ai"]), (0, 50, 200.0, 100.0))
        self.assertEqual((last["from_m"], last["to_m"], last["you"]), (950, 1000, 0.0))
        self.assertEqual(len(L["segments"]), 20)

    def test_fuel_saving_points_and_missing_data(self):
        L = report_layers.fuel(lap(fuel_slope=1.2), lap(fuel_slope=1.0), LENGTH)
        self.assertTrue(L["available"])
        s0 = L["segments"][0]
        self.assertGreater(s0["you"], s0["ai"])                              # you burn more in 0-500 m
        self.assertAlmostEqual(s0["ai"], 0.004 * 1000 * 50, delta=25)       # 4 g per metre-ish over 50 m
        self.assertFalse(report_layers.fuel(lap(with_fuel=False), lap(), LENGTH)["available"])

    def test_tyre_wear_share_and_coarse_data(self):
        L = report_layers.tyres(lap(), lap(wear_slope=0.5), LENGTH)
        self.assertTrue(L["available"])
        hot = [s for s in L["segments"] if 400 <= s["from_m"] < 700]
        cold = [s for s in L["segments"] if s["from_m"] >= 800]
        self.assertGreater(min(s["you"] for s in hot), max(s["you"] for s in cold))
        self.assertAlmostEqual(sum(s["you"] for s in L["segments"]), 100.0, delta=3)
        self.assertFalse(report_layers.tyres(lap(with_wear=False), lap(), LENGTH)["available"])

    def test_braking_points_matched_and_unmatched(self):
        L = report_layers.braking(lap(brake_at=410), lap(brake_at=390))
        self.assertTrue(L["available"])
        it = L["items"][0]
        self.assertEqual((it["you_m"], it["ai_m"], it["diff_m"]), (410, 390, 20))     # you brake 20 m later
        self.assertEqual((it["you_entry_kmh"], it["you_peak_pct"]), (250, 80))
        # a braking zone only the AI has is listed too
        ai = lap(brake_at=390)
        ai["samples"].append((995.0, 19900, 120, 0.0, 0.9, 3, 0.0))
        ai["samples"].append((999.0, 19980, 100, 0.0, 0.9, 3, 0.0))
        items = report_layers.braking(lap(brake_at=410), ai)["items"]
        self.assertEqual(len(items), 2)
        self.assertIsNone(items[1]["you_m"])

    def test_acceleration_points(self):
        corners = [{"corner": 1, "type": "slow", "you": {"apex_speed": 90, "full_throttle_at_m": 600},
                    "them": {"apex_speed": 92, "full_throttle_at_m": 630}}]
        L = report_layers.acceleration(corners, lap(full_at=600), lap(full_at=630))
        it = L["items"][0]
        self.assertEqual((it["you_m"], it["ai_m"], it["diff_m"]), (600, 630, -30))      # you are on full throttle 30 m earlier
        self.assertEqual(it["you_apex_kmh"], 90)
        self.assertIsNotNone(it["you_exit_kmh"])
        self.assertFalse(report_layers.acceleration([], lap(), lap())["available"])

    def test_build_all(self):
        out = report_layers.build(lap(), lap(), lap(), [], LENGTH)
        self.assertEqual(sorted(out), ["acceleration", "battery", "braking", "fuel", "tyres"])
        none = report_layers.build(lap(), None, None, [], LENGTH)
        self.assertTrue(all(not v["available"] for v in none.values()))


if __name__ == "__main__":
    unittest.main()
