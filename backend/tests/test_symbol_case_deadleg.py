# -*- coding: utf-8 -*-
"""纪律卖腿「大小写死腿」回归测试（2026-09-17 生产事故）。

事故：`t_monitor._check_profit_take` / 斐波目标路径把 `_normalize_symbol()` 的**腾讯小写形态**
（`sz002587`）直接写进 `t_triggers`；而网关账本 `get_sellable_ledger()` 的键来自
`paper_positions`（大写 `SZ002587`），`validate_order_at` 原样 `ledger.get(symbol)` → 查不到
→ 恒判「无足够可卖底仓（裸空拦截）」⇒ 这两类纪律卖腿**结构性执行不了**。
生产实测：09-14 起小写触发 62 条（profit_take 40 + fib 22），executed = 0；同期大写
`wolf_defensive_t_reduce` 正常成交 5 笔。回测同病（基线全年 150 条 profit_take 全小写）。

修复：①写库前 `std_symbol()` 归一（治本）；②网关新增 `ledger_item()` 大小写/形态容错（兜底）；
③无底仓预筛也走 `ledger_item`（否则会把"有底仓的小写触发"误判成无底仓而拦掉）。
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading",
           REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import app.services.t_monitor as M                      # noqa: E402
from app.services import t_gateway as G                  # noqa: E402
from app.services.t_data_sources import std_symbol       # noqa: E402

HELD = {"SZ002587": {"symbol": "SZ002587", "volume": 4600, "sellable": 4600, "today_buy": 0}}


class StdSymbolTest(unittest.TestCase):
    def test_forms(self):
        for raw, want in [("sz002587", "SZ002587"), ("SZ002587", "SZ002587"),
                          ("002587.SZ", "SZ002587"), ("600519.SH", "SH600519"),
                          ("sh600519", "SH600519"), ("600519", "SH600519"), (" 000001 ", "SZ000001")]:
            self.assertEqual(std_symbol(raw), want, raw)

    def test_unknown_untouched(self):
        self.assertEqual(std_symbol("BAD"), "BAD")
        self.assertEqual(std_symbol(""), "")


class LedgerItemTest(unittest.TestCase):
    def test_case_tolerant(self):
        for form in ("SZ002587", "sz002587", "002587.SZ", "sz002587 "):
            self.assertIsNotNone(G.ledger_item(HELD, form.strip()) if form.strip() else None, form)

    def test_missing_returns_none(self):
        self.assertIsNone(G.ledger_item(HELD, "SH600519"))
        self.assertIsNone(G.ledger_item(None, "SZ002587"))

    def test_resolve_sell_cap_lowercase_symbol(self):
        # 全程 mock，不碰 DB（否则会去连 5432 卡住）
        with mock.patch.object(G, "get_sellable_ledger", lambda acc=None: HELD), \
             mock.patch.object(G, "base_floor_shares", lambda *a, **k: 0):
            cap = G.resolve_sell_cap("sz002587", "stock")
        self.assertGreater(cap, 0, "小写形态必须能查到可卖额度（旧行为恒 0 → 裸空拦截）")


class InsertNormalisesSymbolTest(unittest.TestCase):
    def setUp(self):
        M.prescreen_cache_clear()   # 预筛缓存按 bar 存活，测试间必须清
        self.calls = []
        p = mock.patch.object(M.t_db, "insert_trigger",
                              lambda trig, status="pending": (self.calls.append((dict(trig), status)) or 1))
        p.start(); self.addCleanup(p.stop)
        # 卖腿会过无底仓预筛 → 必须给账本，否则会去连 DB（无 DATABASE_URL 时卡住）
        q = mock.patch.object(G, "get_sellable_ledger", lambda acc=None: HELD)
        q.start(); self.addCleanup(q.stop)

    def test_profit_take_lowercase_is_stored_uppercase(self):
        M.TMonitor()._insert_wolf_trigger("sz002587", "wolf_profit_take_sell",
                                         {"current": 6.97}, "浮盈2.65%≥2.0%")
        trig, _ = self.calls[-1]
        self.assertEqual(trig["symbol"], "SZ002587", "写库必须是系统标准形态，否则网关查不到底仓")

    def test_fib_lowercase_is_stored_uppercase(self):
        M.TMonitor()._insert_wolf_trigger("sh603259", "wolf_fib_target_sell",
                                         {"current": 160.0}, "斐波目标")
        self.assertEqual(self.calls[-1][0]["symbol"], "SH603259")


class PrescreenNotFooledByCaseTest(unittest.TestCase):
    """无底仓预筛必须用容错取项：有底仓的小写触发不能被误拦。"""

    def setUp(self):
        self.calls = []
        p = mock.patch.object(M.t_db, "insert_trigger",
                              lambda trig, status="pending": (self.calls.append((dict(trig), status)) or 1))
        p.start(); self.addCleanup(p.stop)
        q = mock.patch.object(G, "get_sellable_ledger", lambda acc=None: HELD)
        q.start(); self.addCleanup(q.stop)

    def test_lowercase_sell_with_position_passes_prescreen(self):
        M.TMonitor()._queue_trigger({"account_id": "stock", "symbol": "sz002587",
                                     "event_type": "wolf_profit_take_sell", "direction": "sell",
                                     "quote_price": 6.97, "mode": "auto", "reason": "小赚兑现"})
        trig, status = self.calls[-1]
        self.assertEqual(status, "pending", "有底仓（大写键）不该被无底仓预筛拦掉")
        self.assertEqual(trig["symbol"], "SZ002587")


if __name__ == "__main__":
    unittest.main(verbosity=2)
