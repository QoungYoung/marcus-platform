# -*- coding: utf-8 -*-
"""defense_cash G0-G3 仲裁单测（2026-10-25 纯删除后）。

口径：
  · G3 = 0%（下跌趋势不做）；G2 = 30%；G1 = 50%；G0 不设总仓上限
  · 4-3 筑底 + 无 G3 → 封顶 G1
  · 账户回撤/15% 折中已删除，不再测试
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("WOLF_DISCIPLINE_CFG_DB", "0")
os.environ["DEFENSE_CASH"] = "1"

from app.services import defense_cash as DC  # noqa: E402
from app.services import position_tier as PT  # noqa: E402


def _patch(wave, flags=()):
    DC._CACHE.update({"at": 0.0, "state": None})
    return mock.patch.multiple(
        DC,
        _wave=mock.Mock(return_value=wave),
        _macro_flags=mock.Mock(return_value=set(flags)),
    )


class DefenseCashTest(unittest.TestCase):
    def _eval(self, wave, flags=(), segment=None):
        p = _patch(wave, flags)
        with p:
            DC._CACHE.update({"at": 0.0, "state": None})
            return DC.evaluate(segment=segment)

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
                        flags=("margin_burst",), segment="4-3")
        self.assertEqual(st["level"], "G1", st)
        self.assertEqual(st["target_pct"], 50.0)

    def test_g0_build_no_cap(self):
        st = self._eval({"operation": "build", "level": "d3", "sub_level": "3-3"})
        self.assertEqual(st["level"], "G0")
        self.assertIsNone(st["target_pct"])
        self.assertTrue(st["allow_add"])

class GateIntegrationTest(unittest.TestCase):
    def test_p3_g3_blocks_all_intents(self):
        with mock.patch.object(DC, "current_level", return_value=3), \
             mock.patch.object(DC, "current_target_pct", return_value=0.0):
            for intent in ("new_base", "add_base", "refill_base", "t_refill"):
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
            self.assertEqual(d["cash_floor_pct"], 100.0)

    def test_p3_g2_blocks_new_add_allows_t(self):
        with mock.patch.object(DC, "current_level", return_value=2), \
             mock.patch.object(DC, "current_target_pct", return_value=30.0):
            d = PT.three_tier_gate(
                ts_code="600000.SH", intent="new_base",
                wave_state={"level": "d4", "sub_level": "4-4", "operation": "build"},
            )
            self.assertFalse(d["intent_allowed"])
            d = PT.three_tier_gate(
                ts_code="600000.SH", intent="t_refill",
                wave_state={"level": "d4", "sub_level": "4-4", "operation": "build"},
                has_base=True,
            )
            self.assertTrue(d["intent_allowed"], d)
            self.assertEqual(d["cash_floor_pct"], 70.0)


if __name__ == "__main__":
    unittest.main()
