# -*- coding: utf-8 -*-
"""入场线（狼大语料）：挂线 13/34/144 + 只在下跌里买 + 破线撤腿。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import t_entry_lines as E  # noqa: E402


class MaTest(unittest.TestCase):
    def test_real_data_has_three_lines(self):
        mas = E.moving_averages("SZ002429", "20260109")
        self.assertEqual(set(mas), {13, 34, 60, 144}, "243 根日线应能算出 13/34/60/144 四根均线")

    def test_nearest_below_ordering(self):
        got = E.nearest_lines_below(7.80, {13: 7.38, 34: 6.915, 144: 5.767})
        self.assertEqual([n for n, _ in got], [13, 34, 144], "按从高到低（由近及远）")

    def test_all_lines_above_returns_none(self):
        self.assertIsNone(E.entry_line_target("SZ002429", 1.00, "20260109"), "全在上方 → 不挂")

    def test_target_is_a_real_line(self):
        t = E.entry_line_target("SZ002429", 7.80, "20260109")
        self.assertAlmostEqual(t, E.moving_averages("SZ002429", "20260109")[13], places=2)
        self.assertLess(t, 7.80)


class DeclineOnlyTest(unittest.TestCase):
    def test_off_always_pass(self):
        self.assertTrue(E.decline_only_ok(10.0, 9.0, enabled=False))

    def test_blocked_when_not_declining(self):
        self.assertFalse(E.decline_only_ok(10.0, 9.5, enabled=True), "现价高于前收 → 不买")
        self.assertFalse(E.decline_only_ok(10.0, 10.0, enabled=True), "平盘也不算下跌")

    def test_allowed_when_declining(self):
        self.assertTrue(E.decline_only_ok(9.4, 9.5, enabled=True))

    def test_fail_open_without_data(self):
        self.assertTrue(E.decline_only_ok(0.0, 9.5, enabled=True))


class LineBreakTest(unittest.TestCase):
    def test_break_detected(self):
        self.assertTrue(E.line_broken(7.30, 7.38, pct=1.0), "跌破 1% 以上 → 撤腿")

    def test_touch_is_not_break(self):
        self.assertFalse(E.line_broken(7.35, 7.38, pct=1.0))
        self.assertFalse(E.line_broken(7.38, 7.38, pct=1.0))


class WiringTest(unittest.TestCase):
    def test_build_t_conditions_uses_line_when_enabled(self):
        import os
        from app.services import t_pool as P
        with mock.patch.dict(os.environ, {"BT_ASOF_DAY": "20260109"}, clear=False), \
             mock.patch.object(P, "ENTRY_LINES", True):
            conds = P.build_t_conditions(7.80, 2.0, symbol="SZ002429", price=7.80)
        low = [c for c in conds if c["trigger_kind"] == "low_buy"][0]
        self.assertAlmostEqual(low["target_price"], 7.38, places=2,
                               msg="低吸挂价应改为下方最近均线（MA13）")

    def test_build_t_conditions_legacy_when_disabled(self):
        from app.services import t_pool as P
        with mock.patch.object(P, "ENTRY_LINES", False):
            conds = P.build_t_conditions(7.80, 2.0, symbol="SZ002429", price=7.80)
        low = [c for c in conds if c["trigger_kind"] == "low_buy"][0]
        self.assertAlmostEqual(low["target_price"], round(7.80 * 0.98, 2), places=2)

    def test_switches_default_off_in_library(self):
        self.assertFalse(E.ENTRY_LINES)
        self.assertFalse(E.DECLINE_ONLY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
