# -*- coding: utf-8 -*-
"""止损扫描"每标的每 bar 只算一次"（WOLF_STOP_SCAN_ONCE_PER_BAR，默认关）单测。

背景：20260202 实测 `stop_loss`=492.4s / `_round`=675.4s，当日 `止损条件缓存 hit=1053 miss=536`
⇒ 扫描被调 ~1589 次（≈3 次/(标的·bar)）、每次 ~310ms。重活不在"读条件"（已缓存），
而在同一 (标的, bar) 被反复重算的波段低点/止损线那一段。`_check_stop_loss` 的 docstring
本就写"每标的每轮一次"，本开关把它兑现；**默认关 ⇒ 旧行为逐字不变**。
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


class StopScanOnceTest(unittest.TestCase):
    def setUp(self):
        import app.services.t_monitor as TM
        self.TM = TM
        TM._SL_DONE.clear(); TM._SL_DONE_BAR["k"] = ""

    def _run(self, once: bool, n: int, bar: str = "202602021030"):
        calls = {"n": 0}
        inst = self.TM.TMonitor.__new__(self.TM.TMonitor)
        inst._stop_done_day = set()

        def _heavy(*a, **k):          # 代表"波段低点/止损线"那段重活
            calls["n"] += 1
            return None
        with mock.patch.object(self.TM, "STOP_SCAN_ONCE", once), \
             mock.patch.object(self.TM, "_bar_key", lambda: bar), \
             mock.patch.object(self.TM, "stop_conds_cached", _heavy), \
             mock.patch.object(self.TM, "_invalidate_stop_conds", lambda *a, **k: None):
            for _ in range(n):
                inst._check_stop_loss("SZ002195", {"current": 10.0},
                                      {"SZ002195": {"sellable": 100}})
        return calls["n"]

    def test_default_off_runs_every_time(self):
        self.assertEqual(self._run(False, 3), 3, "开关关：与旧行为一致（每次都算）")

    def test_on_runs_once_per_symbol_bar(self):
        self.assertEqual(self._run(True, 3), 1, "开关开：同一 (标的, bar) 只算一次")

    def test_on_resets_next_bar(self):
        self.assertEqual(self._run(True, 2, bar="202602021030"), 1)
        self.assertEqual(self._run(True, 2, bar="202602021035"), 1, "换 bar 必须重算")

    def test_distinct_symbols_not_merged(self):
        calls = {"n": 0}
        inst = self.TM.TMonitor.__new__(self.TM.TMonitor)
        inst._stop_done_day = set()

        def _heavy(*a, **k):
            calls["n"] += 1
        with mock.patch.object(self.TM, "STOP_SCAN_ONCE", True), \
             mock.patch.object(self.TM, "_bar_key", lambda: "202602021030"), \
             mock.patch.object(self.TM, "stop_conds_cached", _heavy), \
             mock.patch.object(self.TM, "_invalidate_stop_conds", lambda *a, **k: None):
            for sym in ("SZ002195", "SH603203"):
                inst._check_stop_loss(sym, {"current": 10.0}, {sym: {"sellable": 100}})
        self.assertEqual(calls["n"], 2, "不同标的各算一次")

    def test_cheap_guards_still_apply(self):
        """无底仓仍应早退（守卫不能被去重绕过）。"""
        self.assertEqual(self._run(True, 2), 1)     # 有底仓 → 算 1 次
        calls = {"n": 0}
        inst = self.TM.TMonitor.__new__(self.TM.TMonitor)
        inst._stop_done_day = set()

        def _heavy(*a, **k):
            calls["n"] += 1
        with mock.patch.object(self.TM, "STOP_SCAN_ONCE", True), \
             mock.patch.object(self.TM, "_bar_key", lambda: "202602021030"), \
             mock.patch.object(self.TM, "stop_conds_cached", _heavy):
            inst._check_stop_loss("SZ002195", {"current": 10.0}, {"SZ002195": {"sellable": 0}})
        self.assertEqual(calls["n"], 0, "无底仓必须早退，不做重活")


if __name__ == "__main__":
    unittest.main(verbosity=2)
