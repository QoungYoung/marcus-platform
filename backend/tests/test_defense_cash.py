# -*- coding: utf-8 -*-
"""defense_cash G0-G3 仲裁单测（2026-10-25）。

覆盖：
  · systemic level 2 / wave exit → G3=15%（底仓10+T5，现金85）
  · wave defense / systemic 1 / margin_burst → G2=30%
  · 4-3 筑底 + 无 G3 → 封顶 G1=50%
  · 账户回撤 5/8/12 阶梯
  · P3 gate 在 G2/G3 下禁 new/add；G3 连 refill 也禁
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("WOLF_DISCIPLINE_CFG_DB", "0")
os.environ["DEFENSE_CASH"] = "1"

from app.services import defense_cash as DC  # noqa: E402
from app.services import position_tier as PT  # noqa: E402


def _patch(wave, systemic=0, flags=(), dd=None, segment=None):
    DC._CACHE.update({"at": 0.0, "state": None})
    return mock.patch.multiple(
        DC,
        _wave=mock.Mock(return_value=wave),
        _systemic_level=mock.Mock(return_value=systemic),
        _macro_flags=mock.Mock(return_value=set(flags)),
        _break_actions=mock.Mock(return_value=[]),
    ), dd, segment


class DefenseCashTest(unittest.TestCase):
    def _eval(self, wave, systemic=0, flags=(), dd=None, segment=None, break_actions=None):
        p, dd, segment = _patch(wave, systemic, flags, dd, segment)
        with p:
            DC._CACHE.update({"at": 0.0, "state": None})
            return DC.evaluate(account_drawdown_pct=dd, segment=segment,
                               break_actions=(break_actions if break_actions is not None else []))

    def test_g3_systemic_2(self):
        st = self._eval({"operation": "defense", "level": "down"}, systemic=2)
        self.assertEqual(st["level"], "G3")
        self.assertEqual(st["target_pct"], 15.0)
        self.assertEqual(st["buckets"], {"base": 10, "t": 5, "cash": 85})
        self.assertFalse(st["allow_new"])
        self.assertEqual(st["allow_t"], "reduce_only")

    def test_g3_wave_exit(self):
        st = self._eval({"operation": "exit", "level": "down"})
        self.assertEqual(st["level"], "G3")
        self.assertEqual(st["target_pct"], 15.0)

    def test_g2_defense(self):
        st = self._eval({"operation": "defense", "level": "d4"})
        self.assertEqual(st["level"], "G2")
        self.assertEqual(st["target_pct"], 30.0)
        self.assertFalse(st["allow_add"])

    def test_g2_margin_burst(self):
        st = self._eval({"operation": "side", "level": "d4"}, flags=("margin_burst",))
        self.assertEqual(st["level"], "G2")

    def test_43_bottom_caps_g1(self):
        st = self._eval({"operation": "build", "level": "d4", "sub_level": "4-3"},
                        systemic=0, dd=6.0, segment="4-3")
        self.assertEqual(st["level"], "G1", st)
        self.assertEqual(st["target_pct"], 50.0)

    def test_g0_build(self):
        st = self._eval({"operation": "build", "level": "d3", "sub_level": "3-3"})
        self.assertEqual(st["level"], "G0")
        self.assertEqual(st["target_pct"], 75.0)
        self.assertTrue(st["allow_add"])

    def test_break_b33_is_g3_and_override_zero(self):
        st = self._eval({"operation": "build", "level": "d3"},
                        break_actions=[{"asof": "2026-10-25", "symbol": "SH600000",
                                        "domain": "stock", "severity": "B3-3",
                                        "action": "clear", "target_pct": 0.0,
                                        "allow_zero": "1", "block_new_buy": "1",
                                        "reason": "down-wave"}])
        self.assertEqual(st["level"], "G3")
        self.assertEqual(st["symbol_overrides"]["SH600000"], 0.0)
        self.assertFalse(st["allow_new"])
        self.assertTrue(any("破位B3-3" in r for r in st["reasons"]))

    def test_break_b32_is_g2(self):
        st = self._eval({"operation": "build", "level": "d3"},
                        break_actions=[{"asof": "2026-10-25", "symbol": "000001.SH",
                                        "domain": "index", "severity": "B3-2",
                                        "action": "cap_30", "target_pct": 0.30,
                                        "allow_zero": "0", "block_new_buy": "1",
                                        "reason": "index deep break"}])
        self.assertEqual(st["level"], "G2")
        self.assertEqual(st["symbol_overrides"], {})

    def test_break_b2_alert_vs_sell(self):
        alert = self._eval({"operation": "build", "level": "d3"},
                           break_actions=[{"symbol": "SH600000", "domain": "stock",
                                           "severity": "B2", "action": "alert",
                                           "target_pct": None}])
        self.assertEqual(alert["level"], "G1")
        sell = self._eval({"operation": "build", "level": "d3"},
                          break_actions=[{"symbol": "SH600000", "domain": "stock",
                                          "severity": "B2", "action": "reduce_half",
                                          "target_pct": 0.50}])
        self.assertEqual(sell["level"], "G2")
        self.assertEqual(sell["symbol_overrides"]["SH600000"], 0.50)

    def test_drawdown_ladder(self):
        self.assertEqual(self._eval({"operation": "build"}, dd=5.1)["level"], "G1")
        self.assertEqual(self._eval({"operation": "build"}, dd=8.1)["level"], "G2")
        self.assertEqual(self._eval({"operation": "build"}, dd=12.1)["level"], "G3")


class GateIntegrationTest(unittest.TestCase):
    def test_gate_g3_hard_block(self):
        with mock.patch.object(DC, "current_level", return_value=3), \
             mock.patch.object(DC, "get_state", return_value={"level": "G3", "level_num": 3,
                                                              "target_pct": 15.0,
                                                              "reasons": ["test"]}):
            g = DC.gate_for_entry()
        self.assertTrue(g["hard_block"])
        self.assertEqual(g["level"], "G3")

    def test_p3_g3_blocks_new_add_refill(self):
        with mock.patch.object(DC, "current_level", return_value=3), \
             mock.patch.object(DC, "current_target_pct", return_value=15.0):
            for intent in ("new_base", "add_base", "refill_base"):
                d = PT.three_tier_gate(
                    ts_code="600000.SH", intent=intent,
                    wave_state={"level": "d4", "sub_level": "4-5", "operation": "build"},
                    has_base=True, t_universe=True, mainline_dir=True,
                )
                self.assertFalse(d["intent_allowed"], (intent, d))
                self.assertEqual(d["cap_pct"], 0.0)
            d = PT.three_tier_gate(
                ts_code="600000.SH", intent="t_refill",
                wave_state={"level": "d4", "sub_level": "4-5", "operation": "build"},
                has_base=True,
            )
            self.assertTrue(d["intent_allowed"], d)
            self.assertEqual(d["cash_floor_pct"], 85.0)

    def test_p3_g2_blocks_new_add_but_allows_refill(self):
        with mock.patch.object(DC, "current_level", return_value=2), \
             mock.patch.object(DC, "current_target_pct", return_value=30.0):
            d = PT.three_tier_gate(
                ts_code="600000.SH", intent="new_base",
                wave_state={"level": "d4", "sub_level": "4-4", "operation": "build"},
            )
            self.assertFalse(d["intent_allowed"])
            d = PT.three_tier_gate(
                ts_code="600000.SH", intent="refill_base",
                wave_state={"level": "d4", "sub_level": "4-4", "operation": "build"},
                has_base=True, t_universe=True,
            )
            self.assertTrue(d["intent_allowed"], d)
            self.assertEqual(d["cash_floor_pct"], 70.0)


if __name__ == "__main__":
    unittest.main()
