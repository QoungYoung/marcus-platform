# -*- coding: utf-8 -*-
"""预筛查询**按 bar 缓存**的回归测试（2026-09-17 用户拍板"先按第 1 条做"）。

缓存为什么不改变语义：预筛只负责"提前出局"，**权威判定仍在网关**
（`gateway_execute` 内部用新鲜数据重新校验 `_count_today_trades` / 账本），
所以缓存偏乐观时该笔仍会被网关正确拒绝，结果一致。
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

import app.services.t_monitor as M                       # noqa: E402
from app.services import t_gateway as G                  # noqa: E402

LEDGER = {"SZ002587": {"symbol": "SZ002587", "volume": 4600, "sellable": 4600}}


class PrescreenCacheTest(unittest.TestCase):
    def setUp(self):
        M.prescreen_cache_clear()
        M._PC_STATS.update({"hit": 0, "miss": 0})

    def test_ledger_read_once_per_bar(self):
        calls = {"n": 0}

        def _led(acc=None):
            calls["n"] += 1
            return LEDGER
        with mock.patch.object(G, "get_sellable_ledger", _led), \
             mock.patch.object(M, "PRESCREEN_CACHE", True):
            M._nobase_sell_skip("stock", "SZ002587")
            M._nobase_sell_skip("stock", "SH600519")
            M._nobase_sell_skip("stock", "SH600519")
        self.assertEqual(calls["n"], 1, "同一 bar 内账本只读一次")
        self.assertGreaterEqual(M._PC_STATS["hit"], 2)

    def test_clear_forces_reread(self):
        calls = {"n": 0}

        def _led(acc=None):
            calls["n"] += 1
            return LEDGER
        with mock.patch.object(G, "get_sellable_ledger", _led), \
             mock.patch.object(M, "PRESCREEN_CACHE", True):
            M._nobase_sell_skip("stock", "SZ002587")
            M.prescreen_cache_clear()
            M._nobase_sell_skip("stock", "SZ002587")
        self.assertEqual(calls["n"], 2, "清缓存后必须重新读")

    def test_switch_off_reads_every_time(self):
        calls = {"n": 0}

        def _led(acc=None):
            calls["n"] += 1
            return LEDGER
        with mock.patch.object(G, "get_sellable_ledger", _led), \
             mock.patch.object(M, "PRESCREEN_CACHE", False):
            M._nobase_sell_skip("stock", "SZ002587")
            M._nobase_sell_skip("stock", "SZ002587")
        self.assertEqual(calls["n"], 2)

    def test_sell_count_cached_per_symbol_bar(self):
        calls = {"n": 0}

        def _cnt(acc, sym, side):
            calls["n"] += 1
            return 2
        with mock.patch.object(G, "_count_today_trades", _cnt), \
             mock.patch.object(G, "_trade_cap", lambda: 2), \
             mock.patch.object(M, "PRESCREEN_CACHE", True):
            self.assertIsNotNone(M.sell_cap_exhausted("stock", "SZ002587"))
            self.assertIsNotNone(M.sell_cap_exhausted("stock", "SZ002587"))
            self.assertIsNotNone(M.sell_cap_exhausted("stock", "sz002587"))   # 形态归一后同键
        self.assertEqual(calls["n"], 1, "同一 (账户,标的,bar) 只数一次")

    def test_bar_key_changes_with_clock(self):
        import datetime as _dt
        with mock.patch.object(M, "datetime") as _d:
            _d.now.return_value = _dt.datetime(2026, 1, 16, 10, 5)
            k1 = M._bar_key()
            _d.now.return_value = _dt.datetime(2026, 1, 16, 10, 10)
            k2 = M._bar_key()
        self.assertNotEqual(k1, k2, "换 bar 必须换键（自动失效）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
