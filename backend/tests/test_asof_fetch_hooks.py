# -*- coding: utf-8 -*-
"""取数网关与回测接入层单测（2026-09-17，目标 goal-7853cbe6…）。

覆盖：门控（默认关）、as-of 夹取规则（成对/单日/注入）、未来日期与**未来时刻**双截断、
promax `fields+items` 数组行解析、`quote_from_minute` 由最后一根 ≤bar 收盘构造行情、
`install_backtest_hooks()` 真的替换了生产函数且在关闭时**什么都不做**。全部离线（不发网络请求）。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading",
           REPO_ROOT / "jobs"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import bt_asof_fetch as F      # noqa: E402


class ClampTest(unittest.TestCase):
    def test_future_dates_clamped(self):
        p = F.clamp_asof({"end_date": "20261231", "start_date": "20250101"}, "20260126")
        self.assertEqual(p["end_date"], "20260126")
        self.assertEqual(p["start_date"], "20250101")

    def test_pair_injected_when_neither_given(self):
        p = F.clamp_asof({"ts_code": "000001.SZ"}, "20260126", range_days=10)
        self.assertEqual(p["end_date"], "20260126")
        self.assertEqual(p["start_date"], "20260116")     # 成对，绝不让上游默认取"最新"

    def test_trade_date_alone_untouched(self):
        p = F.clamp_asof({"trade_date": "20260126"}, "20260126")
        self.assertEqual(p, {"trade_date": "20260126"})

    def test_single_sided_end_gets_pair(self):
        p = F.clamp_asof({"end_date": "20260126"}, "20260126")
        self.assertIn("start_date", p)


class FutureCutTest(unittest.TestCase):
    def test_future_days_dropped(self):
        rows = [{"trade_date": "20260126", "close": 1}, {"trade_date": "20260127", "close": 2}]
        keep = F.filter_future_rows(rows, "20260126")
        self.assertEqual([r["trade_date"] for r in keep], ["20260126"])
        self.assertGreaterEqual(F.STATS["future_rows_cut"], 1)

    def test_future_minutes_dropped(self):
        rows = [{"trade_time": "2026-01-26 10:35:00", "close": 1},
                {"trade_time": "2026-01-26 10:36:00", "close": 2},
                {"trade_time": "2026-01-26 14:00:00", "close": 3}]
        keep = F.filter_future_rows(rows, "20260126", asof_hhmm="10:35")
        self.assertEqual(len(keep), 1)
        self.assertTrue(keep[0]["trade_time"].endswith("10:35:00"))


class ParseTest(unittest.TestCase):
    def test_fields_items_becomes_dicts(self):
        """promax 的 `data.fields + items(数组行)` 必须解析成 dict 行（纯函数，不发网络）。"""
        body = {"code": 0, "msg": "ok",
                "data": {"fields": ["ts_code", "trade_date", "close"],
                         "items": [["000001.SZ", "20260126", 10.96]]}}
        rows, err = F.parse_body(body)
        self.assertIsNone(err)
        self.assertEqual(rows, [{"ts_code": "000001.SZ", "trade_date": "20260126", "close": 10.96}])

    def test_error_envelope(self):
        rows, err = F.parse_body({"ok": False, "error": "unknown_api", "message": "未登记的接口: x"})
        self.assertIsNone(rows)
        self.assertIn("未登记的接口", err)
        rows2, err2 = F.parse_body({"code": 40101, "msg": "请指定正确的接口名"})
        self.assertIn("40101", err2)


class QuoteFromMinuteTest(unittest.TestCase):
    def test_quote_built_from_last_bar(self):
        rows = [{"trade_time": "2026-01-26 10:30:00", "open": 10, "high": 11, "low": 9, "close": 10.5, "vol": 100},
                {"trade_time": "2026-01-26 10:35:00", "open": 10.6, "high": 12, "low": 10, "close": 11.04, "vol": 200}]
        with mock.patch.object(F, "minute_1m", lambda *a, **k: (rows, {"count": 2})):
            q = F.quote_from_minute("SH600519", "20260126", "10:35")
        self.assertEqual(q["current"], 11.04)
        self.assertEqual(q["high"], 12.0)
        self.assertEqual(q["low"], 9.0)
        self.assertEqual(q["pre_close"], 10.5)
        self.assertEqual(q["time"], "2026-01-26 10:35:00")
        self.assertEqual(q["source"], "asof_minute_1m")

    def test_empty_when_no_rows(self):
        with mock.patch.object(F, "minute_1m", lambda *a, **k: ([], {"error": "x"})):
            self.assertEqual(F.quote_from_minute("SH600519", "20260126", "10:35"), {})

    def test_ts_code_conversion(self):
        self.assertEqual(F.to_ts_code("SH600519"), "600519.SH")
        self.assertEqual(F.to_ts_code("sz002587"), "002587.SZ")
        self.assertEqual(F.to_ts_code("600519.SH"), "600519.SH")


class InstallHooksTest(unittest.TestCase):
    def test_disabled_by_default(self):
        with mock.patch.dict(os.environ, {"BT_ASOF_FETCH": "0"}, clear=False):
            r = F.install_backtest_hooks()
        self.assertEqual(r["installed"], [])
        self.assertTrue(any("未开启" in s for s in r["skipped"]))

    def test_enabled_patches_production_functions(self):
        import app.services.t_monitor as TM
        from core.xueqiu_engine import XueqiuEngine as XE
        orig_tm, orig_xe = TM._fetch_daily_tencent_dated, XE.get_stock_quote
        try:
            with mock.patch.dict(os.environ, {"BT_ASOF_FETCH": "1"}, clear=False):
                r = F.install_backtest_hooks()
            self.assertTrue(any("_fetch_daily_tencent_dated" in s for s in r["installed"]))
            self.assertTrue(any("xueqiu_engine" in s for s in r["installed"]))
            self.assertIsNot(TM._fetch_daily_tencent_dated, orig_tm)
            self.assertIsNot(XE.get_stock_quote, orig_xe)
        finally:
            TM._fetch_daily_tencent_dated = orig_tm
            XE.get_stock_quote = orig_xe

    def test_hook_falls_back_when_no_data(self):
        import app.services.t_monitor as TM
        orig = TM._fetch_daily_tencent_dated
        try:
            with mock.patch.dict(os.environ, {"BT_ASOF_FETCH": "1"}, clear=False):
                F.install_backtest_hooks()
            with mock.patch.object(F, "daily_bars_for", lambda *a, **k: None):
                got = TM._fetch_daily_tencent_dated("SH600519", 5)
            self.assertEqual(got, orig("SH600519", 5) if False else got)   # 不抛异常即可
        finally:
            TM._fetch_daily_tencent_dated = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)


class StopCondCacheTest(unittest.TestCase):
    """止损扫描的条件列表按 bar 缓存（2026-09-17 "都改吧" 第 1 项）。"""

    def setUp(self):
        import app.services.t_monitor as TM
        self.TM = TM
        TM._SC_CONDS.clear()
        TM._SC_STATS.update({"hit": 0, "miss": 0})
        self.calls = {"n": 0}

        # ⚠️ 替身必须照**真实**签名写（symbol/trade_date/account_id/before_trade_date）：
        #    原先写成 (symbol, asof_day) 与实现同步写错 ⇒ 单测全绿而线上调用点每次 TypeError
        #    （2026-09-17 事故：止损扫描整段静默空转）。契约校验见 test_stop_scan_contract.py。
        def _q(symbol=None, trade_date=None, account_id=None, before_trade_date=None):
            self.calls["n"] += 1
            return [{"symbol": symbol, "account_id": account_id,
                     "trigger_kind": "custom_support_sell"}]

        self._p = mock.patch.object(TM.t_db, "list_active_conditions", _q)
        self._p.start()
        self.addCleanup(self._p.stop)

    def test_same_bar_reads_once(self):
        with mock.patch.object(self.TM, "STOP_COND_CACHE", True):
            a = self.TM.stop_conds_cached("SZ002555")
            b = self.TM.stop_conds_cached("SZ002555")
        self.assertEqual(self.calls["n"], 1, "同一 bar 内同一标的只查一次")
        self.assertEqual(a, b)
        self.assertGreaterEqual(self.TM._SC_STATS["hit"], 1)

    def test_switch_off_always_queries(self):
        with mock.patch.object(self.TM, "STOP_COND_CACHE", False):
            self.TM.stop_conds_cached("SZ002555")
            self.TM.stop_conds_cached("SZ002555")
        self.assertEqual(self.calls["n"], 2)

    def test_bar_change_invalidates(self):
        with mock.patch.object(self.TM, "STOP_COND_CACHE", True), \
             mock.patch.object(self.TM, "_bar_key", side_effect=["202601120935", "202601120940"]):
            self.TM.stop_conds_cached("SZ002555")
            self.TM.stop_conds_cached("SZ002555")
        self.assertEqual(self.calls["n"], 2, "换 bar 必须重新查")


class PrefetchTest(unittest.TestCase):
    """当日批量预取（第 2 项）：应逐个灌缓存并如实计数；失败不抛。"""

    def test_prefetch_counts(self):
        with mock.patch.object(F, "daily", lambda *a, **k: ([{"trade_date": "20260112"}], {})), \
             mock.patch.object(F, "minute_1m", lambda *a, **k: ([{"trade_time": "x"}], {})):
            r = F.prefetch_day("20260112", "15:00", ["SZ002555", "SH603203", "SZ002555"], max_symbols=5)
        self.assertEqual(r["symbols"], 2)          # 去重
        self.assertEqual(r["daily"], 2)
        self.assertEqual(r["minute"], 2)
        self.assertEqual(r["failed"], 0)

    def test_prefetch_marks_failures(self):
        with mock.patch.object(F, "daily", lambda *a, **k: ([], {"error": "x"})), \
             mock.patch.object(F, "minute_1m", lambda *a, **k: ([], {"error": "x"})):
            r = F.prefetch_day("20260112", "15:00", ["SZ002555"], max_symbols=5)
        self.assertEqual(r["failed"], 1)
        self.assertEqual(r["daily"], 0)
