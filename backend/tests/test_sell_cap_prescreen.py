# -*- coding: utf-8 -*-
"""卖腿日额度预筛回归测试（2026-09-17 用户要求：G6「一个票最多卖 2 笔」用尽后**不要一直报**）。

现象：条件卖腿（custom_support_sell 等）在支撑位反复命中 → 每次写一条触发行、每次被 G6 拒
（`[G6] 当日 SYM sell 已 2 笔 ≥ 上限 2`）→ 触发列表与日志刷屏。
处置：入队口 `TMonitor._queue_trigger` 先按网关同一口径判额度；用尽 → 不写行、只按
(标的,账户,日) 记一次并累计抑制次数；止损/破位豁免；取数异常 fail-open。
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

import app.services.t_monitor as M                    # noqa: E402
from app.services import t_gateway as G               # noqa: E402

HELD = {"SH603383": {"symbol": "SH603383", "volume": 5000, "sellable": 5000}}


def _sell_trig(**over):
    t = {"account_id": "stock", "condition_id": 7, "symbol": "SH603383",
         "event_type": "custom_support_sell", "quote_price": 37.61, "mode": "auto",
         "reason": "支撑位卖出", "direction": "sell"}
    t.update(over)
    return t


class SellCapPrescreenTest(unittest.TestCase):
    def setUp(self):
        M._SELL_CAP_SUPPRESSED.clear()
        M.prescreen_cache_clear()   # 预筛缓存按 bar 存活，测试间必须清
        self.calls = []
        p = mock.patch.object(M.t_db, "insert_trigger",
                              lambda trig, status="pending": (self.calls.append((dict(trig), status)) or 1))
        p.start(); self.addCleanup(p.stop)
        q = mock.patch.object(G, "get_sellable_ledger", lambda acc=None: HELD)
        q.start(); self.addCleanup(q.stop)
        self.mon = M.TMonitor()

    def _quota(self, cap, done):
        for name, val in (("_trade_cap", lambda: cap), ("_count_today_trades", lambda a, s, side: done)):
            pp = mock.patch.object(G, name, val); pp.start(); self.addCleanup(pp.stop)

    def test_exhausted_suppresses_row_and_reports_once(self):
        self._quota(2, 2)
        self.assertIsNone(self.mon._queue_trigger(_sell_trig()))
        self.assertIsNone(self.mon._queue_trigger(_sell_trig()))     # 第二次同样抑制
        self.assertEqual(self.calls, [], "额度用尽不得再写触发行")
        self.assertEqual(M._SELL_CAP_SUPPRESSED[("stock", "SH603383",
                                                 __import__('datetime').datetime.now().strftime("%Y%m%d"))], 2)

    def test_below_cap_still_proceeds(self):
        self._quota(2, 1)
        self.assertEqual(self.mon._queue_trigger(_sell_trig()), 1)
        self.assertEqual(self.calls[-1][1], "pending")

    def test_stop_loss_exempt(self):
        self._quota(2, 5)
        self.assertEqual(self.mon._queue_trigger(_sell_trig(event_type="stop_loss", reason="止损卖出")), 1)

    def test_cap_disabled(self):
        self._quota(0, 9)
        self.assertEqual(self.mon._queue_trigger(_sell_trig()), 1)

    def test_counter_error_fails_open(self):
        def _boom(*a, **k):
            raise RuntimeError("db down")
        for name, val in (("_trade_cap", lambda: 2), ("_count_today_trades", _boom)):
            pp = mock.patch.object(G, name, val); pp.start(); self.addCleanup(pp.stop)
        self.assertEqual(self.mon._queue_trigger(_sell_trig()), 1, "统计失败必须放行，不能误拦")

    def test_switch_off_restores_old_behaviour(self):
        self._quota(2, 2)
        with mock.patch.object(M, "SELL_CAP_PRESCREEN", False):
            self.assertEqual(self.mon._queue_trigger(_sell_trig()), 1)

    def test_buy_leg_unaffected(self):
        self._quota(2, 2)
        self.assertEqual(self.mon._queue_trigger(_sell_trig(event_type="wolf_zheng_t_buy", direction="buy")), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
