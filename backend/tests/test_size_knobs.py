# -*- coding: utf-8 -*-
"""建仓规模三旋钮 + 买腿最小有效规模（2026-09-18 用户"都改"）。

实测驱动（暖启 ON 臂 0105→0203，去重后可复核）：
  · 单笔上限 = 净值 10% ⇒ 25 万账户 25,000 元/笔，实测 7 笔 24–28k 与参数吻合；
  · 总量上限 55%（语料 65%）、单标 15%；
  · 18 笔里 11 笔 ≤1 万、其中 7 笔仅 100 股（≈2,000 元）；22 天只买到 3 只标的 ⇒ 暴露 ~11%。
"""
import os
import unittest
from unittest import mock

from app.services import t_build as TB

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ParamKnobTest(unittest.TestCase):
    def test_default_off(self):
        self.assertFalse(TB.WOLF_BUILD_SIZE_CORPUS)          # 库内默认关（env 未设时）
        src = open(os.path.join(ROOT, "app", "services", "t_build.py"), encoding="utf-8").read()
        self.assertIn('os.getenv("WOLF_BUILD_SIZE_CORPUS", "0")', src)

    def test_override_values_are_corpus_caliber(self):
        self.assertEqual(TB.BUILD_SIZE_CORPUS_OVERRIDES["single_order_pct"]["std"], 0.15)
        self.assertEqual(TB.BUILD_SIZE_CORPUS_OVERRIDES["total_floor_cap"]["std"], 0.65)
        self.assertEqual(TB.BUILD_STEP_FRACTION, 3.0)

    def test_params_merge_respects_db_and_switch(self):
        base = {"no_rebuild_symbols": ["SH515880"]}
        with mock.patch.object(TB.t_db, "get_build_params", return_value=base):
            off = TB._params()
            self.assertEqual(off["single_order_pct"]["std"], 0.10)
            self.assertEqual(off["total_floor_cap"]["std"], 0.55)
            with mock.patch.object(TB, "WOLF_BUILD_SIZE_CORPUS", True):
                on = TB._params()
            self.assertEqual(on["single_order_pct"]["std"], 0.15)
            self.assertEqual(on["total_floor_cap"]["std"], 0.65)
            self.assertEqual(on["single_order_pct"]["cons"], 0.06)     # 只动 std 档
            self.assertEqual(on["total_floor_cap"]["agg"], 0.70)
        # DB 显式给了该档 ⇒ 不夺权
        with mock.patch.object(TB.t_db, "get_build_params",
                               return_value={"single_order_pct": {"std": 0.08}}), \
             mock.patch.object(TB, "WOLF_BUILD_SIZE_CORPUS", True):
            self.assertEqual(TB._params()["single_order_pct"]["std"], 0.08)


class GapSizingTest(unittest.TestCase):
    """B：建仓"少笔足量" —— 缺口/3 与单标剩余空间取小。"""

    def _size(self, equity=250000.0, floor_value=0.0, symbol_value=0.0, price=25.0, on=True):
        with mock.patch.object(TB, "WOLF_BUILD_SIZE_CORPUS", on), \
             mock.patch.object(TB.t_db, "get_build_params", return_value={}), \
             mock.patch.object(TB, "compute_regime", return_value={"regime": "ACTIVE"}):
            return TB.build_sizing("SZ000001", price, net_asset=equity,
                                   total_floor_value=floor_value, symbol_value=symbol_value,
                                   regime="ACTIVE")

    def test_gap_step_raises_single_order(self):
        off = self._size(on=False)
        on = self._size(on=True)
        self.assertEqual(off["tier"], "std")
        # 关：单笔 = 净值 × 10% = 25,000 → 25 元/股 = 1000 股
        self.assertEqual(off["suggest_volume"], 1000)
        # 开：缺口 = 65% × 25 万 = 162,500，/3 = 54,167 → 但受单标 15%(37,500) 约束 → 1500 股
        self.assertEqual(on["suggest_volume"], 1500)
        self.assertLessEqual(on["suggest_volume"] * 25.0, 250000.0 * 0.15 + 0.01)

    def test_no_false_total_cap_rejection_when_on(self):
        """单笔 ≤ 缺口 ⇒ 不再误触「总底仓超上限」（原口径在 floor 接近 55% 时会拒单）。"""
        r = self._size(floor_value=135000.0, on=True)
        self.assertNotIn("总底仓超上限", str(r.get("reason") or ""))
        self.assertTrue(r["pass"], r)
        # 缺口 27,500 < 单笔上限 37,500 ⇒ 单笔收敛到缺口（补满即止），不再误拒
        self.assertLessEqual(r["suggest_volume"] * 25.0, 27500.0 + 0.01)
        self.assertGreaterEqual(r["suggest_volume"] * 25.0, 24000.0)

    def test_symbol_room_limits_size(self):
        # 单标已 36,000/37,500 → 只剩 1,500 元 ⇒ 远小于满额 1,500 股；只剩 A 股最小单位 100 股
        # （""100 股保底"" 是既有语义：room 不足一手时仍放行一手，不静默变 0 股）
        r = self._size(symbol_value=36000.0, on=True)
        self.assertEqual(r["suggest_volume"], 100)
        self.assertLess(r["suggest_volume"], 1500)


class MinNotionalTest(unittest.TestCase):
    def test_gateway_wires_knob(self):
        src = open(os.path.join(ROOT, "app", "services", "t_gateway.py"), encoding="utf-8").read()
        self.assertIn('os.getenv("WOLF_MIN_BUY_NOTIONAL", "0")', src)
        self.assertIn("低于最小有效规模", src)
        self.assertIn("volume = _need", src)

    def test_driver_enables_all_knobs(self):
        src = open(os.path.join(ROOT, "..", "jobs", "bt_prod_run.py"), encoding="utf-8").read()
        self.assertIn('("WOLF_BUILD_SIZE_CORPUS", "1")', src)
        self.assertIn('("WOLF_MIN_BUY_NOTIONAL", "8000")', src)


if __name__ == "__main__":
    unittest.main()
