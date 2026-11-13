import threading
import unittest
from types import SimpleNamespace

from race_engineer import coach
from race_engineer.tools import ToolError
from tests.test_report_layers import LENGTH, lap


def slow(l, from_m, extra_ms, time_ms):
    """Copy of a lap that loses extra_ms from from_m onward."""
    l = dict(l)
    l["samples"] = [(d, t + (extra_ms if d >= from_m else 0)) + tuple(rest) for d, t, *rest in l["samples"]]
    l["time_ms"] = time_ms
    return l


def state(my_laps, ai_laps=None, pos=None, store=2e6):
    builders = [SimpleNamespace(laps=my_laps), SimpleNamespace(laps=ai_laps or {})]
    return SimpleNamespace(lock=threading.RLock(), player_slot=lambda: 0,
                           live_laps=SimpleNamespace(builders=builders, length=LENGTH),
                           lap_data=[{"lapDistance": pos}, {}], car_status=[{"ersStoreEnergy": store}, {}])


class CoachTests(unittest.TestCase):
    def test_brake_later(self):
        best = lap(brake_at=400)
        late = slow(lap(brake_at=380), 380, 300, 20300)       # braked 20 m earlier, lost 0.3 s
        r = coach.where_losing(state({(1, 1, 0): best, (1, 2, 0): late}))
        top = r["losses"][0]
        self.assertEqual(top["fix"], "brake 20 m later")
        self.assertIn("braking zone 1", top["place"])
        self.assertIn("Try to brake 20 m later", r["answer"])

    def test_last_lap_is_best_and_missing_data(self):
        r = coach.where_losing(state({(1, 1, 0): slow(lap(), 0, 500, 20500), (1, 2, 0): lap()}))
        self.assertEqual(r["losses"], [])
        with self.assertRaises(ToolError):
            coach.where_losing(state({(1, 1, 0): lap()}))
        with self.assertRaises(ToolError):
            coach.where_losing(state({(1, 1, 0): lap(), (1, 2, 0): lap()}), "ai")

    def test_deploy_now_and_hold(self):
        best = lap(kw=100.0)                                   # deploys 0-500 m
        inside = coach.deploy_now(state({(1, 1, 0): best}, pos=200.0))
        self.assertEqual(inside["action"], "deploy")
        outside = coach.deploy_now(state({(1, 1, 0): best}, pos=700.0))
        self.assertEqual(outside["action"], "hold")
        self.assertEqual(outside["distance_to_zone_m"], 300)    # next zone starts at 0 m = 1000 - 700
        empty = coach.deploy_now(state({(1, 1, 0): best}, pos=200.0, store=0.1e6))
        self.assertEqual(empty["action"], "empty")
        with self.assertRaises(ToolError):
            coach.deploy_now(state({}, pos=200.0))

    def test_deploy_more_out_of_braking_zone(self):
        def with_kw(l, kw):                                    # battery power only on the exit 460-700 m
            l = dict(l, deployed_J=1e6)
            l["ers"] = [(d, t, s, kw * 1000 if 460 <= d < 700 else 0.0, m, f) for d, t, s, _, m, f in l["ers"]]
            return l
        best = with_kw(lap(), 150.0)
        mine = with_kw(lap(), 50.0)
        mine["time_ms"] = 20200
        r = coach.deploy_more(state({(1, 1, 0): best, (1, 2, 0): mine}))
        self.assertEqual(len(r["exits"]), 1)
        self.assertEqual(r["exits"][0]["zone"], 1)
        self.assertAlmostEqual(r["exits"][0]["diff_kw"], 100, delta=10)      # 50 m bins cut the edge of the exit
        self.assertIn("out of braking zone 1", r["answer"])
        # equal battery use: nothing to report
        same = coach.deploy_more(state({(1, 1, 0): best, (1, 2, 0): dict(best, time_ms=20200)}))
        self.assertEqual(same["exits"], [])


if __name__ == "__main__":
    unittest.main()
