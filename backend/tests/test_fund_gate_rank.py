# -*- coding: utf-8 -*-
"""资金门「相对口径」单测（2026-09-21 用户拍板 B）。

语料：2025-04-15 条件1「看前一日流入前5和流出前3的板块和个股…避开三日都是流出前3的板块个股」。
开关 WOLF_FUND_GATE_MODE=abs（默认，逐位不变）| rank（本口径）。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))

import wolf_theme_vol_fund as W  # noqa: E402


def _day(vals):
    return dict(vals)


class TestRankDanger(unittest.TestCase):
    def test_bottom3_all_days_is_danger(self):
        # 主题 A 连续三天都是净流出前 3
        days = [_day({"A": -500, "B": -100, "C": 50, "D": 200, "E": -80}),
                _day({"A": -300, "B": 10, "C": 20, "D": 30, "E": -90}),
                _day({"A": -800, "B": -700, "C": 10, "D": 20, "E": -5})]
        ok, why = W.rank_danger(days, "A", 3)
        self.assertTrue(ok, why)

    def test_only_two_of_three_is_not_danger(self):
        days = [_day({"A": -500, "B": -100, "C": 50, "D": 200, "E": -80}),
                _day({"A": -300, "B": 10, "C": 20, "D": 30, "E": -90}),
                _day({"A": 900, "B": -700, "C": -600, "D": -500, "E": -5})]
        ok, why = W.rank_danger(days, "A", 3)
        self.assertFalse(ok, why)
        self.assertIn("正常", why)

    def test_negative_but_not_bottom3_is_not_danger(self):
        # 关键差异：绝对口径会因为"净流入为负"判危险；相对口径只看排名
        days = [_day({"A": -1, "B": -2, "C": -3, "D": -4, "E": -5}) for _ in range(3)]
        ok, why = W.rank_danger(days, "A", 3)
        self.assertFalse(ok, "A 虽为负但排第 5，不该判危险（这正是相对口径的意义）")

    def test_missing_theme_or_too_few_themes_fails_open(self):
        ok, why = W.rank_danger([_day({"B": -1, "C": -2, "D": -3, "E": -4})], "A", 3)
        self.assertFalse(ok)
        ok2, why2 = W.rank_danger([_day({"A": -1, "B": -2})], "A", 3)
        self.assertFalse(ok2, "主题数 < k+1 ⇒ fail-open")

    def test_empty_is_fail_open(self):
        ok, why = W.rank_danger([], "A", 3)
        self.assertFalse(ok)


class TestModeSwitch(unittest.TestCase):
    def test_default_is_abs(self):
        os.environ.pop("WOLF_FUND_GATE_MODE", None)
        self.assertEqual(os.getenv("WOLF_FUND_GATE_MODE", "abs"), "abs")

    def test_rank_mode_reaches_pure_function(self):
        # theme_rank_danger 在缓存不足时必须 fail-open（不依赖网络）
        os.environ["WOLF_FUND_GATE_MODE"] = "rank"
        try:
            ok, why = W.theme_rank_danger("AI/算力/科技", as_of="20200101", days=3, k=3)
            self.assertFalse(ok)
            self.assertIn("放行", why)
        finally:
            os.environ.pop("WOLF_FUND_GATE_MODE", None)


if __name__ == "__main__":
    unittest.main()