# -*- coding: utf-8 -*-
"""指数分钟取数回归（`bt_prod_run.LocalMarket` 的 mins1 回退 + 指数昨收）—— 2026-09-25。

## 病灶（账本 §9.29）

`bt_local_market.build_market(mode=5m)` 委托 `bt_prod_run.LocalMarket`，而它的 `load_symbol`
**只 glob `mins_dir`**（`mins_adj/`）。四个指数（000300/000852/000016/399001）的 5min 文件
**只落在 `mins1/` 与 `mins1_adj/`** ⇒ 指数报价全部取不到（`market_missing` 75/75 天）
⇒ **黄白线不可用** ⇒ 谨慎闸 4 条子条件里 3 条（黄白线类）从未触发。
兄弟类 `bt_local_market.LocalMarket1m._paths` 早有「5min 也看 mins1_dir」的回退 ⇒ 本类补齐同口径。

另修：指数在 `bars.sqlite` 里**没有日线行** ⇒ `pre_close()` 原先回退成"今日开盘"，
使 `change_pct` 变成"开盘至今"（黄白线直接吃这个数，幅度被系统性压小）⇒ 改为**前一交易日最后一根 bar 的收盘**。

## 本测试守什么

1. 四个指数在 5min 市场里**能取到报价**（且 `missing` 不含它们）；
2. 股票标的取数**不受影响**（回归护栏）；
3. 指数 `pre_close` == 上一交易日 5min 最后一根的收盘（而不是今日开盘）；
4. 静态接线：`load_symbol` 有 mins1 回退、`build_market` 透传 `mins1_dir`。
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "jobs"))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import bt_local_market as blm  # noqa: E402

DAY = "20260129"
MINS = os.path.join(ROOT, "data", "_bt_full", "mins_adj")
MINS1 = os.path.join(ROOT, "data", "_bt_full", "mins1_adj")
BARS = os.path.join(ROOT, "data", "_bt_full", "bars.sqlite")
HAVE = all(os.path.isdir(p) for p in (MINS, MINS1)) and os.path.exists(BARS)


@unittest.skipUnless(HAVE, "缺分钟夹具/日线库")
class IndexQuoteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["WOLF_IDX_MINS1_FALLBACK"] = "1"     # 修复开关（默认 0=旧行为，见 §9.29/§9.34）
        cls.mkt = blm.build_market(DAY, mins1=MINS1, mins=MINS, bars_db=BARS, mode="5m")

    def test_default_off_means_no_index_quote(self):
        """开关关 ⇒ 旧行为：指数取不到报价（T13/T14/T15 跑的就是这个行为）。"""
        os.environ.pop("WOLF_IDX_MINS1_FALLBACK", None)
        try:
            m = blm.build_market(DAY, mins1=MINS1, mins=MINS, bars_db=BARS, mode="5m")
            self.assertIsNone(m.quote("SH000852", "10:00"), "默认应为旧行为（无指数报价）")
        finally:
            os.environ["WOLF_IDX_MINS1_FALLBACK"] = "1"

    def test_index_quotes_available(self):
        for sym in ("SH000852", "SH000300", "SH000016", "SZ399001"):
            q = self.mkt.quote(sym, "10:00")
            self.assertIsNotNone(q, "指数 %s 取不到报价（mins1 回退失效）" % sym)
            self.assertGreater(q.get("current") or 0, 0)
            self.assertTrue(q.get("change_pct") is not None, "缺 change_pct（黄白线要它）")

    def test_index_not_missing(self):
        self.mkt.quote("SH000852", "10:00")
        self.assertNotIn("SH000852", self.mkt.missing)

    def test_stock_unaffected(self):
        q = self.mkt.quote("SH600584", "10:00")
        self.assertIsNotNone(q, "股票取数被改坏了（回归）")

    def test_index_pre_close_is_prev_day_close(self):
        """昨收 = 上一交易日 5min 最后一根收盘（不是今日开盘）。"""
        files = sorted(os.listdir(MINS1))
        prevs = [f for f in files if f.startswith("000852_SH_5min_") and f.split("_")[-1][:8] < DAY]
        self.assertTrue(prevs, "缺前一交易日文件")
        prev = max(f.split("_")[-1][:8] for f in prevs)
        j = json.load(open(os.path.join(MINS1, "000852_SH_5min_%s.json" % prev), encoding="utf-8"))
        bars = [b for b in j["bars"] if b[5] is not None]
        want = float(bars[-1][5])
        got = self.mkt.pre_close("SH000852")
        self.assertAlmostEqual(got, want, places=2, msg="昨收 %.2f 应为前一日收盘 %.2f" % (got, want))

    def test_wiring(self):
        src = open(os.path.join(ROOT, "jobs", "bt_prod_run.py"), encoding="utf-8").read()
        self.assertIn("self.mins1_dir", src)
        self.assertIn('"%s_%s_5min_*.json" % (code, ex)', src)
        fac = open(os.path.join(ROOT, "jobs", "bt_local_market.py"), encoding="utf-8").read()
        self.assertIn("mins1_dir=mins1", fac, "工厂未把 mins1 透传给 5m 市场")
        self.assertIn("前一交易日最后一根", src, "pre_close 未修为「前一日收盘」")


if __name__ == "__main__":
    unittest.main()
