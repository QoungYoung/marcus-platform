# -*- coding: utf-8 -*-
"""主升浪五维评价引擎测试（复刻「短线炒股分析员」）。

用 2026-08-20 收盘数据校验三只样本票的关键判定是否与人工分析一致：
- 600613 神奇制药：末期加速赶顶 / RSI6=100 / 5个主升浪涨停 / 底部涨幅+83.7%
- 002412 汉森制药：加速初期 / RSI6=99 / 洗盘7/24 / 1个缺口 8.42-9.26 / 一字板买不进
- 002081 金螳螂：已结束 / 高点回撤-19.8% / 趋势sideways / 不碰反弹离场

依赖 Tushare 实时数据，无网络时自动跳过。
"""
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _tushare_ok() -> bool:
    try:
        from core._api_config import get_tushare_pro
        get_tushare_pro()
        return True
    except Exception:
        return False


@unittest.skipUnless(_tushare_ok(), "Tushare 不可用")
class TestMainWaveAnalyzer(unittest.TestCase):
    AS_OF = "20260820"

    @classmethod
    def setUpClass(cls):
        from core.main_wave_analyzer import DataFetcher, analyze_stock
        cls.fetcher = DataFetcher(use_akshare=False)
        cls.cache = {}

    def _analyze(self, symbol: str):
        if symbol not in self.cache:
            from core.main_wave_analyzer import analyze_stock
            a = analyze_stock(symbol, fetcher=self.fetcher, as_of=self.AS_OF)
            self.assertNotIn("error", a, f"{symbol} 分析失败: {a.get('error')}")
            self.cache[symbol] = a
        return self.cache[symbol]

    # ── 神奇制药 600613 ──
    def test_600613_shenqi_stage(self):
        a = self._analyze("600613")
        self.assertEqual(a["verdict"]["stage"], "末期加速赶顶")
        self.assertEqual(a["verdict"]["risk_level"], "极高")
        self.assertEqual(a["verdict"]["advice"], "不碰/不追高")

    def test_600613_shenqi_tech(self):
        a = self._analyze("600613")
        five = a["five"]
        self.assertTrue(five["ma"]["aligned"])
        self.assertEqual(round(five["rsi"]["rsi6"]), 100)
        self.assertEqual(a["structure"]["main_wave_lu_count"], 5)
        self.assertAlmostEqual(a["structure"]["run_from_base"], 83.7, delta=2)
        self.assertEqual(a["structure"]["base_date"], "20260721")

    def test_600613_shenqi_red_flags(self):
        a = self._analyze("600613")
        red = " ".join(a["verdict"]["red_flags"])
        for kw in ("RSI6=100", "天量滞涨", "大股东减持", "异动"):
            self.assertIn(kw, red)

    # ── 汉森制药 002412 ──
    def test_002412_hansen_stage(self):
        a = self._analyze("002412")
        self.assertEqual(a["verdict"]["stage"], "加速初期")
        self.assertIn("一字板买不进", a["verdict"]["advice"])
        self.assertEqual(a["structure"]["main_wave_lu_count"], 2)

    def test_002412_hansen_washout(self):
        a = self._analyze("002412")
        self.assertEqual(a["structure"]["washout"]["date"], "20260724")
        self.assertEqual(round(a["five"]["rsi"]["rsi6"]), 99)
        self.assertAlmostEqual(a["fundamental"]["pe_ttm"], 20.68, delta=1)
        gaps = a["five"]["gap"]["unfilled"]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["date"], "20260820")

    # ── 金螳螂 002081 ──
    def test_002081_jintanglang_stage(self):
        a = self._analyze("002081")
        self.assertEqual(a["verdict"]["stage"], "已结束，进入回调")
        self.assertIn("不碰", a["verdict"]["advice"])
        self.assertEqual(a["five"]["trend"]["state"], "sideways")
        self.assertAlmostEqual(a["structure"]["high_pullback"], -19.8, delta=1)

    def test_002081_jintanglang_vol_price(self):
        a = self._analyze("002081")
        self.assertTrue(a["five"]["vol_price"]["tianliang_top"])
        self.assertTrue(a["five"]["vol_price"]["has_limit_down"])

    def test_002081_jintanglang_base(self):
        a = self._analyze("002081")
        self.assertEqual(a["structure"]["base_date"], "20260721")
        self.assertAlmostEqual(a["structure"]["run_from_base"], 66.7, delta=1)

    # ── 通用 ──
    def test_markdown_contains_sections(self):
        a = self._analyze("600613")
        for sec in ("主升浪技术画像", "K线走势还原", "五维判定", "今日盘面数据",
                    "资金面信号", "基本面风险", "结论", "观察信号", "评分"):
            self.assertIn(sec, a["markdown"])

    def test_score_range(self):
        for sym in ("600613", "002412", "002081"):
            a = self._analyze(sym)
            self.assertGreaterEqual(a["score"]["score"], 0)
            self.assertLessEqual(a["score"]["score"], 100)


if __name__ == "__main__":
    unittest.main()
