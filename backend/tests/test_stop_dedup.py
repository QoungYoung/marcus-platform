# -*- coding: utf-8 -*-
"""止损**重复执行**回归测试（2026-09-11 生产事故）。

事故（生产 2026-09-11 09:36:25 / 09:36:29, SH588170）：
  `t_monitor._round` 是 `for cond in conditions:` 逐条件调用 `_check_stop_loss`，
  而原去抖查的是 `t_triggers WHERE event_type='stop_loss'` —— **这条路径从来不写这种行**
  （成功时只 `update_condition_state(armed=0)`）→ 查询恒为空 → 去抖形同虚设。
  同标的两个条件 → 同一轮执行两次「盘中减半仓」17000+17000 = 34000 全部清光，
  违反狼大「底仓不卖」铁律（本意只减半）。

修复：①内存当日去抖 `self._stop_done_day`（同轮/同日即时生效）；
      ②成功后在 t_triggers 落一行 `event_type='stop_loss'`、**status='executed'（终态）**
        作为审计与跨重启去抖依据（不能用 pending —— `claim_pending_trigger` 无差别认领 pending）。
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import app.services.t_monitor as M  # noqa: E402


class _FakeDB:
    def __init__(self, scalar):
        self._scalar = scalar

    def execute(self, *a, **k):
        return self

    def scalar(self):
        return self._scalar

    def close(self):
        pass


def _make_monitor():
    mon = M.TMonitor()
    mon._today_bars = lambda s: [{"high": 1.0, "low": 0.99, "close": 1.0}]
    mon._prev_daily = lambda s, n=5: [{"high": 1.1, "low": 0.9, "close": 1.0, "vol": 1.0}]
    mon._daily_dated = lambda s, n=40: [{"date": "2026%02d%02d" % (8, d), "close": 1.0,
                                         "high": 1.05, "low": 0.99, "vol": 1.0} for d in range(1, 20)]
    mon._buy_date = lambda s, t=None: "2026-08-05"
    return mon


def _patch_all(scalar=None):
    """打桩出一套"两个条件、同一标的、止损价被跌破"的环境。"""
    conds = [{"id": 1, "symbol": "SH588170", "stop_loss_price": 1.00, "trigger_kind": "low_buy"},
             {"id": 2, "symbol": "SH588170", "stop_loss_price": 1.00, "trigger_kind": "high_sell"}]
    return [
        mock.patch.object(M.t_db, "list_active_conditions", return_value=conds),
        mock.patch.object(M.t_db, "update_condition_state", return_value=True),
        mock.patch.object(M.t_db, "insert_trigger", return_value=1),
        # _stop_close_confirm 的第一个实参里会先算 t_build._params()（读 DB）→ 必须一起打桩，
        # 否则测试会卡在真实数据库连接上（本机 PG 不通）
        mock.patch("app.services.t_build._params", return_value={}),
        mock.patch.object(M, "_stop_close_confirm", return_value=None),   # None = 不拦，按原口径执行
        mock.patch.object(M, "_stop_time_ok", return_value=(True, "")),
        mock.patch.object(M, "_in_close_window", return_value=False),     # 盘中 → 减半仓
        mock.patch("app.database.SessionLocal", lambda: _FakeDB(scalar)),
    ]


class TestStopDoesNotRepeat(unittest.TestCase):
    def test_two_conditions_same_symbol_executes_once(self):
        mon = _make_monitor()
        calls = []
        patchers = _patch_all(scalar=None)
        for p in patchers:
            p.start()
        try:
            with mock.patch("app.services.t_gateway.gateway_execute",
                                   side_effect=lambda *a, **k: (calls.append(a), {"status": "success"})[1]):
                q = {"current": 0.95}
                led = {"SH588170": {"sellable": 34000}}
                mon._check_stop_loss("SH588170", q, led)   # 条件1
                mon._check_stop_loss("SH588170", q, led)   # 条件2（同一轮）
        finally:
            for p in patchers:
                p.stop()
        self.assertEqual(len(calls), 1, "同标的同轮只应执行一次止损（原 bug 会执行两次）")
        self.assertEqual(calls[0][3], 17000, "盘中档位应为减半仓 17000（不是 34000 全清）")

    def test_audit_row_is_terminal_not_pending(self):
        """审计行必须是终态 —— 留 pending 会被 claim_pending_trigger 认领并再卖一次。"""
        mon = _make_monitor()
        rows = []
        patchers = _patch_all(scalar=None)
        for p in patchers:
            p.start()
        try:
            M.t_db.insert_trigger.side_effect = lambda trig, status="pending": rows.append(
                (trig["event_type"], status))
            with mock.patch("app.services.t_gateway.gateway_execute", return_value={"status": "success"}):
                mon._check_stop_loss("SH588170", {"current": 0.95}, {"SH588170": {"sellable": 34000}})
        finally:
            for p in patchers:
                p.stop()
        self.assertEqual(rows, [("stop_loss", "executed")])

    def test_db_guard_blocks_when_row_exists(self):
        """跨重启去抖：库里当日已有 stop_loss 行 → 不执行。"""
        mon = _make_monitor()
        calls = []
        patchers = _patch_all(scalar=1)          # 已有行
        for p in patchers:
            p.start()
        try:
            with mock.patch("app.services.t_gateway.gateway_execute",
                                   side_effect=lambda *a, **k: (calls.append(a), {"status": "success"})[1]):
                mon._check_stop_loss("SH588170", {"current": 0.95}, {"SH588170": {"sellable": 34000}})
        finally:
            for p in patchers:
                p.stop()
        self.assertEqual(calls, [])

    def test_reject_does_not_mark_done(self):
        """网关拒绝（如 sellable=0）不应打上"当日已止损"标记 —— 否则当天真跌破也不再处理。"""
        mon = _make_monitor()
        patchers = _patch_all(scalar=None)
        for p in patchers:
            p.start()
        try:
            with mock.patch("app.services.t_gateway.gateway_execute", return_value={"status": "rejected"}):
                mon._check_stop_loss("SH588170", {"current": 0.95}, {"SH588170": {"sellable": 34000}})
        finally:
            for p in patchers:
                p.stop()
        self.assertEqual(len(mon._stop_done_day), 0)


class TestCloseWindowClears(unittest.TestCase):
    def test_close_window_clears_all(self):
        """收盘确认档位仍是清仓（含底仓）—— 与狼大"收盘跌破我才出"一致。"""
        mon = _make_monitor()
        calls = []
        patchers = _patch_all(scalar=None)
        patchers[6] = mock.patch.object(M, "_in_close_window", return_value=True)
        for p in patchers:
            p.start()
        try:
            with mock.patch("app.services.t_gateway.gateway_execute",
                                   side_effect=lambda *a, **k: (calls.append(a), {"status": "success"})[1]):
                mon._check_stop_loss("SH588170", {"current": 0.95}, {"SH588170": {"sellable": 34000}})
        finally:
            for p in patchers:
                p.stop()
        self.assertEqual(calls[0][3], 34000)


if __name__ == "__main__":
    unittest.main()
