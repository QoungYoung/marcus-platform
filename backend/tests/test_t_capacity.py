# -*- coding: utf-8 -*-
"""做T仓位两分法（WOLF_T_CAPACITY_2TIER）单测：底仓≥65% + 日内T≤20%（语料 2026-09-03）。"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import t_capacity as C  # noqa: E402


class TwoTierTest(unittest.TestCase):
    def test_targets(self):
        t = C.two_tier(250000, 100000)
        self.assertAlmostEqual(t["base_target"], 162500.0, places=2)   # 65%
        self.assertAlmostEqual(t["base_gap"], 62500.0, places=2)
        self.assertAlmostEqual(t["t_budget"], 50000.0, places=2)       # 20%
        self.assertAlmostEqual(t["exposure_pct"], 40.0, places=2)

    def test_gap_zero_when_above_floor(self):
        self.assertEqual(C.two_tier(250000, 200000)["base_gap"], 0.0)


class ClampBuyTest(unittest.TestCase):
    def test_build_boosted_to_min_effective(self):
        """无底仓建仓：把 200 股这种碎单抬到最小有效规模（治回测实测的 200 股/6983 元）。"""
        v, why = C.clamp_buy_volume(200, 30.0, 250000, 0.0, is_build=True)
        self.assertGreaterEqual(v * 30.0, C.MIN_BUILD_NOTIONAL * 0.99, why)
        self.assertIn("抬量", why)

    def test_build_capped_by_gap_third(self):
        v, why = C.clamp_buy_volume(10**9, 30.0, 250000, 0.0, is_build=True)
        cap = (250000 * 0.65 / 3.0) / 30.0
        self.assertLessEqual(v, cap + 100, why)

    def test_build_blocked_when_base_full(self):
        v, why = C.clamp_buy_volume(200, 30.0, 250000, 200000, is_build=True)
        self.assertEqual(v, 0)
        self.assertIn("65%", why)

    def test_t_sleeve_clamped(self):
        """有底仓（做T/加仓）：受 20% 日内T 预算约束。"""
        v, why = C.clamp_buy_volume(10**9, 100.0, 250000, 100000, is_build=False)
        self.assertLessEqual(v * 100.0, 250000 * 0.20 + 1, why)
        self.assertIn("日内T", why)

    def test_t_sleeve_allows_small(self):
        v, why = C.clamp_buy_volume(300, 30.0, 250000, 100000, is_build=False)
        self.assertEqual(v, 300)

    def test_fail_open_inputs(self):
        self.assertEqual(C.clamp_buy_volume(0, 30.0, 250000, 0, True)[0], 0)
        self.assertEqual(C.clamp_buy_volume(100, 0.0, 250000, 0, True)[0], 100)

    def test_default_off(self):
        self.assertFalse(C.enabled(), "开关必须默认关（生产行为不变）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
