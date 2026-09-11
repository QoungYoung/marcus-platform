# -*- coding: utf-8 -*-
"""U9 主题容量约束（相对分位）单测。

狼大原话: 2026-09-02 楼729「**小票就太多了 不好判断**」; 楼733「农业拉10个点带动的资金量不过100E」。
审计 U9 记录: 此前 capacity_amt20_yi **只输出提示、不拦截**。
用户 2026-09-11 决策: 用**相对分位**（主题内强度前 X% 才可买），不用绝对名额。
阈值语料未给 → 默认 WOLF_THEME_QUANTILE_PCT=50，水平由回测校准。
"""
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "apps" / "main_line", REPO_ROOT / "backend"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import wolf_confirm_pick as W  # noqa: E402


def rows(n, start=0.9, step=0.05):
    return [{"ts": "S%02d" % i, "leader": round(start - i * step, 3)} for i in range(n)]


class TestThemeQuantileKeep(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.pop("WOLF_THEME_QUANTILE_PCT", None)

    def tearDown(self):
        os.environ.pop("WOLF_THEME_QUANTILE_PCT", None)
        if self._old is not None:
            os.environ["WOLF_THEME_QUANTILE_PCT"] = self._old

    def test_top_half_of_ten(self):
        k, d, cut = W.theme_quantile_keep(rows(10), pct=50, key="leader")
        self.assertEqual(len(k), 5)
        self.assertEqual(d, 5)
        self.assertEqual([r["ts"] for r in k], ["S00", "S01", "S02", "S03", "S04"])
        self.assertAlmostEqual(cut, 0.7, places=3)   # 第 k(5) 名的强度

    def test_top_30pct_of_ten(self):
        k, d, _ = W.theme_quantile_keep(rows(10), pct=30, key="leader")
        self.assertEqual((len(k), d), (3, 7))

    def test_ceil_on_odd_counts(self):
        # 7 只取前 50% → ceil(3.5) = 4
        k, _, _ = W.theme_quantile_keep(rows(7), pct=50, key="leader")
        self.assertEqual(len(k), 4)

    def test_hundred_pct_keeps_all_and_zero_disables(self):
        self.assertEqual(len(W.theme_quantile_keep(rows(10), pct=100, key="leader")[0]), 10)
        self.assertEqual(len(W.theme_quantile_keep(rows(10), pct=0, key="leader")[0]), 10)

    def test_env_switch(self):
        os.environ["WOLF_THEME_QUANTILE_PCT"] = "20"
        self.assertEqual(len(W.theme_quantile_keep(rows(10), key="leader")[0]), 2)
        os.environ["WOLF_THEME_QUANTILE_PCT"] = "0"
        self.assertEqual(len(W.theme_quantile_keep(rows(10), key="leader")[0]), 10)

    def test_default_is_50(self):
        self.assertEqual(len(W.theme_quantile_keep(rows(10), key="leader")[0]), 5)

    def test_ties_at_boundary_all_kept(self):
        """并列在第 k 名的一并保留 —— 不按序位切并列（否则又把序位噪声当强度差）。"""
        same = [{"ts": "T%d" % i, "leader": 0.5} for i in range(6)]
        k, d, _ = W.theme_quantile_keep(same, pct=50, key="leader")
        self.assertEqual((len(k), d), (6, 0))

    def test_none_leader_ranks_last(self):
        r = [{"ts": "A", "leader": None}, {"ts": "B", "leader": 0.9},
             {"ts": "C", "leader": 0.8}, {"ts": "D", "leader": 0.7}]
        k, _, _ = W.theme_quantile_keep(r, pct=50, key="leader")
        self.assertEqual([x["ts"] for x in k], ["B", "C"])

    def test_single_and_empty(self):
        self.assertEqual(len(W.theme_quantile_keep([{"ts": "A", "leader": 1.0}], pct=50)[0]), 1)
        self.assertEqual(len(W.theme_quantile_keep([], pct=50)[0]), 0)

    def test_kept_preserves_input_order(self):
        k, _, _ = W.theme_quantile_keep(rows(6), pct=50, key="leader")
        self.assertEqual([r["ts"] for r in k], ["S00", "S01", "S02"])


class TestRoundtripSellUp(unittest.TestCase):
    def test_default_is_wolf_lower_bound(self):
        """狼大「3-5个点」→ 默认取区间下沿 3%（原 0.008 是自设的小止盈）。"""
        from app.services import roundtrip_sell as RS
        self.assertAlmostEqual(RS.ROUNDTRIP_SELL_UP, 0.03, places=6)


if __name__ == "__main__":
    unittest.main()
