# -*- coding: utf-8 -*-
"""做T目标幅度对齐狼大语料（`WOLF_T_TARGET_CORPUS`）单测。

语料依据（`docs/wolf-behavior-blueprint.md:204/447`）：
  「目标 3-4 个点」「ETF 出 2 个点即合格」「2 点力度不够就收工」
  「千万不要 1 个点的波动用糖葫芦战法」（2026-08-20 / 2026-09-02）
判据：**开关默认关 ⇒ 行为与以前逐字一致**；置 1 才把高抛下限抬到 3.0%（ETF 2.0%）；
低吸下限**不变**（语料未给低吸数值，不自行编造）。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading",
           REPO_ROOT / "jobs"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import t_pool as P  # noqa: E402


def _legs(cost, amp, symbol=None, switch=False):
    with mock.patch.object(P, "T_TARGET_CORPUS", switch):
        cs = P.build_t_conditions(cost, amp_med=amp, symbol=symbol)
    return {c["trigger_kind"]: c for c in cs}


class TargetCorpusTest(unittest.TestCase):
    def test_switch_off_is_legacy(self):
        """默认关：高抛 = 成本 +1.5%（振幅小时由下限决定）。"""
        d = _legs(10.0, 1.0)
        self.assertAlmostEqual(d["high_sell_then_buy_back"]["sell_target_price"], 10.15, places=2)
        self.assertAlmostEqual(d["low_buy"]["target_price"], 9.80, places=2)

    def test_switch_on_stock_floor_3pct(self):
        d = _legs(10.0, 1.0, symbol="SZ002195", switch=True)
        self.assertAlmostEqual(d["high_sell_then_buy_back"]["sell_target_price"], 10.30, places=2,
                               msg="语料：个股做T目标 3-4 个点 ⇒ 下限 3.0%")

    def test_switch_on_etf_floor_2pct(self):
        d = _legs(10.0, 1.0, symbol="SH510300", switch=True)
        self.assertAlmostEqual(d["high_sell_then_buy_back"]["sell_target_price"], 10.20, places=2,
                               msg="语料：ETF 出 2 个点即合格 ⇒ 下限 2.0%")

    def test_low_buy_unchanged_both_ways(self):
        off = _legs(10.0, 1.0, symbol="SZ002195", switch=False)["low_buy"]["target_price"]
        on = _legs(10.0, 1.0, symbol="SZ002195", switch=True)["low_buy"]["target_price"]
        self.assertAlmostEqual(off, on, places=4, msg="低吸下限不得被本开关改动")

    def test_amp_dominates_when_large(self):
        """振幅大时由 amp×0.75 决定，开关不影响。"""
        off = _legs(10.0, 8.0, symbol="SZ002195", switch=False)["high_sell_then_buy_back"]["sell_target_price"]
        on = _legs(10.0, 8.0, symbol="SZ002195", switch=True)["high_sell_then_buy_back"]["sell_target_price"]
        self.assertAlmostEqual(off, 10.60, places=2)     # 10 × (1 + 8×0.75/100) = 10.60
        self.assertAlmostEqual(on, off, places=4)

    def test_unknown_symbol_treated_as_stock(self):
        d = _legs(10.0, 1.0, symbol=None, switch=True)
        self.assertAlmostEqual(d["high_sell_then_buy_back"]["sell_target_price"], 10.30, places=2)

    def test_stop_loss_untouched(self):
        off = _legs(10.0, 1.0, symbol="SZ002195", switch=False)["low_buy"]["stop_loss_price"]
        on = _legs(10.0, 1.0, symbol="SZ002195", switch=True)["low_buy"]["stop_loss_price"]
        self.assertAlmostEqual(off, on, places=4, msg="止损口径不在本次改动范围")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class CorpusBuyGateTest(unittest.TestCase):
    """② 买腿三道前置门（`WOLF_T_GATE_CORPUS`，默认关）。"""

    def test_default_off_always_pass(self):
        from app.services import t_pool as P
        with mock.patch.object(P, "T_GATE_CORPUS", False):
            self.assertEqual(P.corpus_buy_block("SZ002195", has_position=False), "")
            self.assertEqual(P.corpus_buy_block("SZ002195", has_position=True), "")

    def test_regime_blocked(self):
        from app.services import t_pool as P
        with mock.patch.object(P, "T_GATE_CORPUS", True), \
             mock.patch.object(P, "current_low_buy_gate", lambda: "BLOCKED"):
            r = P.corpus_buy_block("SZ002195", has_position=True)
        self.assertIn("BLOCKED", r)

    def test_qualification_requires_position(self):
        from app.services import t_pool as P
        with mock.patch.object(P, "T_GATE_CORPUS", True), \
             mock.patch.object(P, "current_low_buy_gate", lambda: "ALLOWED"):
            r = P.corpus_buy_block("SZ002195", has_position=False)
        self.assertIn("资格", r)
            # 有底仓 + 无位置门风险 → 放行
        with mock.patch.object(P, "T_GATE_CORPUS", True), \
             mock.patch.object(P, "current_low_buy_gate", lambda: "ALLOWED"), \
             mock.patch.object(P, "position_gate_reason", lambda: ""):
            self.assertEqual(P.corpus_buy_block("SZ002195", has_position=True), "")

    def test_position_gate(self):
        from app.services import t_pool as P
        with mock.patch.object(P, "T_GATE_CORPUS", True), \
             mock.patch.object(P, "current_low_buy_gate", lambda: "ALLOWED"), \
             mock.patch.object(P, "position_gate_reason", lambda: "指数处整数关口"):
            r = P.corpus_buy_block("SZ002195", has_position=True)
        self.assertIn("位置", r)

    def test_position_gate_default_off(self):
        """位置门默认关：8 月的整数关口水位不适用于全局窗口（用户 2026-09-18 纠正）。"""
        from app.services import t_pool as P
        self.assertFalse(P.T_POSITION_GATE)
        self.assertEqual(P.position_gate_reason(), "", "默认必须不产生位置门理由")

    def test_regime_gate_written_when_on(self):
        from app.services import t_pool as P
        with mock.patch.object(P, "T_GATE_CORPUS", True), \
             mock.patch.object(P, "current_low_buy_gate", lambda: "BLOCKED"):
            conds = P.build_t_conditions(10.0, 1.0, symbol="SZ002195")
        self.assertEqual(conds[0]["regime_gate"], "BLOCKED")
        with mock.patch.object(P, "T_GATE_CORPUS", False):
            conds2 = P.build_t_conditions(10.0, 1.0, symbol="SZ002195")
        self.assertEqual(conds2[0]["regime_gate"], "ALLOWED", "开关关时必须仍是 ALLOWED（旧行为）")
