# -*- coding: utf-8 -*-
"""bt_fund_asof 单测：as-of 财务/估值取数层（补齐 leader v2 三项缺口的底座）。

重点验证「**不穿越未来**」：同一报告期常有多次公告（修订版），as-of 必须取 ann_date ≤ cut 的那一版；
以及符号形式（pipeline 传 SH600895，DB/缓存是 600895.SH）—— 认错形式会让三块静默全 None。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "jobs"))

import bt_fund_asof as F  # noqa: E402


class TestNorm(unittest.TestCase):
    def test_both_forms(self):
        for src, dst in (("SH600895", "600895.SH"), ("SZ002371", "002371.SZ"),
                         ("BJ920165", "920165.BJ"), ("600895.SH", "600895.SH"),
                         ("002371", "002371.SZ"), ("600000", "600000.SH"),
                         ("sh600895", "600895.SH")):
            self.assertEqual(F._norm(src), dst, src)

    def test_garbage(self):
        self.assertEqual(F._norm(""), "")
        self.assertEqual(F._norm(None), "")


class TestAsOf(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fundtest_")
        self._root, self._data = F.ROOT, F.DATA
        F.ROOT = self.tmp
        F.DATA = self.tmp
        for k in ("fina_by_code", "fina", "daily_basic", "forecast"):
            os.makedirs(os.path.join(self.tmp, "T", k), exist_ok=True)
        # 同一报告期两版公告：Q3 正式 + 次年 1/23 的年报（对未来不可见）
        with open(os.path.join(self.tmp, "T", "fina_by_code", "600895.SH.json"), "w") as f:
            json.dump([
                {"ann_date": "20251031", "end_date": "20250930", "or_yoy": 19.09,
                 "netprofit_yoy": 20.66, "revenue": 100.0, "n_income": 10.0, "src": "income"},
                {"ann_date": "20260123", "end_date": "20251231", "or_yoy": 99.0,
                 "netprofit_yoy": 99.0, "revenue": 200.0, "n_income": 20.0, "src": "income"},
            ], f, ensure_ascii=False)
        with open(os.path.join(self.tmp, "T", "daily_basic", "20260108.json"), "w") as f:
            json.dump({"600895.SH": {"pe_ttm": 66.5, "pb": 3.3, "total_mv": 3e6, "circ_mv": 2e6}}, f)
        with open(os.path.join(self.tmp, "T", "forecast", "20260123.json"), "w") as f:
            json.dump({"600895.SH": {"ann_date": "20260123", "end_date": "20251231",
                                     "type": "预增", "pmin": 90.0, "pmax": 120.0}}, f)

    def tearDown(self):
        F.ROOT, F.DATA = self._root, self._data
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fina_uses_version_visible_at_cut(self):
        st = F.earnings_state("SH600895", "20260108", "T")
        self.assertTrue(st["ok"])
        self.assertEqual(st["ann_date"], "20251031", "必须取 ≤cut 的那一版，不能拿次年 1/23 的年报")
        self.assertAlmostEqual(st["rev_yoy"], 19.09, places=2)

    def test_future_forecast_invisible(self):
        st = F.earnings_state("SH600895", "20260108", "T")
        self.assertIsNone(st["ftype"], "1/23 才公告的预告在 1/8 不可见（未来函数）")
        st2 = F.earnings_state("SH600895", "20260123", "T")
        self.assertEqual(st2["ftype"], "预增", "到 1/23 才可见")
        self.assertEqual(st2["flag"], "good")

    def test_val_from_daily_basic(self):
        st = F.val_state("SH600895", "20260108", "T")
        self.assertTrue(st["ok"])
        self.assertAlmostEqual(st["pe_ttm"], 66.5, places=2)
        self.assertEqual(st["flag"], "flat")

    def test_fail_open_on_missing(self):
        self.assertEqual(F.earnings_state("SH600895", "20250101", "T")["flag"], "unknown")
        self.assertFalse(F.val_state("SH600895", "20200101", "T")["ok"])
        self.assertFalse(F.val_state("SZ000001", "20260108", "T")["ok"])

    def test_chain_uncovered_is_missing_not_negative(self):
        st = F.chain_state(code="SH600895", name="不存在的名字")
        self.assertFalse(st["ok"], "链字典未覆盖 ⇒ 缺失(fail-open)，绝不能当成'非中枢'扣分")
        self.assertEqual(st["score_hint"], 0.0)


class TestYoyFallback(unittest.TestCase):
    def test_two_period_income_yoy(self):
        tmp = tempfile.mkdtemp(prefix="fundtest2_")
        old = F.ROOT
        F.ROOT = tmp
        try:
            os.makedirs(os.path.join(tmp, "T", "fina_by_code"), exist_ok=True)
            with open(os.path.join(tmp, "T", "fina_by_code", "600895.SH.json"), "w") as f:
                json.dump([
                    {"ann_date": "20241031", "end_date": "20240930", "revenue": 100.0, "n_income": 10.0},
                    {"ann_date": "20251031", "end_date": "20250930", "revenue": 150.0, "n_income": 5.0},
                ], f)
            y = F._yoy_from_income("SH600895", "20260108", "T")
            self.assertAlmostEqual(y["or_yoy"], 50.0, places=2)
            self.assertAlmostEqual(y["netprofit_yoy"], -50.0, places=2)
        finally:
            F.ROOT = old
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
