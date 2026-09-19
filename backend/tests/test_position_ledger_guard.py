# -*- coding: utf-8 -*-
"""持仓台账护栏回归测试（2026-09-19 生产事故）。

事故：backend 的 VN.PY 模拟盘引擎只认走 gateway 的成交，直连 DB 路径落下的成交
（orderid stock_order0000xx / ORD0000xx）它的内存持仓看不见，却每 3 秒
（paper_account_setting.json timer_interval=3）把过期快照写回 paper_positions：
生产实测 SH588170 台账 2900 股 / 库里被盖成 7000 股 →
  · jobs/recon_account_cash.py 持仓线对账失败（还被文案误报成「漂移超阈值」）；
  · 卖腿/止损按 paper_positions 推量 ⇒ 可能卖出并不存在的 4100 股。

修复：写回前查台账（paper_trades 未作废）；有成交 ⇒ 以台账 FIFO 为准，
无成交（外部同步仓）⇒ 回退旧行为。开关 VNPY_POSITION_LEDGER_GUARD。
"""
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "core"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


def _install_vnpy_stub() -> None:
    """vnpy 只装在容器里；这里塞最小 stub，好在本地跑通事件写库路径。"""
    if "vnpy" in sys.modules:
        return
    vnpy = types.ModuleType("vnpy")
    vnpy.__path__ = []
    trader = types.ModuleType("vnpy.trader")
    trader.__path__ = []
    ev = types.ModuleType("vnpy.trader.event")
    for name in ("EVENT_ORDER", "EVENT_TRADE", "EVENT_ACCOUNT", "EVENT_POSITION"):
        setattr(ev, name, name)
    obj = types.ModuleType("vnpy.trader.object")
    for name in ("OrderData", "TradeData", "AccountData", "PositionData"):
        setattr(obj, name, type(name, (), {}))
    const = types.ModuleType("vnpy.trader.constant")


    class _Enum:
        def __init__(self, value):
            self.value = value

    class Direction(_Enum):
        LONG = _Enum("多")
        SHORT = _Enum("空")
        NET = _Enum("净")

    class Status(_Enum):
        SUBMITTING = _Enum("提交中")
        NOTTRADED = _Enum("未成交")
        PARTTRADED = _Enum("部分成交")
        ALLTRADED = _Enum("全部成交")
        CANCELLED = _Enum("已撤销")
        REJECTED = _Enum("拒单")

    const.Direction, const.Status = Direction, Status
    event_engine = types.ModuleType("vnpy.event")
    event_engine.Event, event_engine.EventEngine = type("Event", (), {}), type("EventEngine", (), {})
    for name, mod in (("vnpy", vnpy), ("vnpy.trader", trader), ("vnpy.trader.event", ev),
                      ("vnpy.trader.object", obj), ("vnpy.trader.constant", const),
                      ("vnpy.event", event_engine)):
        sys.modules[name] = mod


_install_vnpy_stub()

import app.core.trading.position_ledger as PL  # noqa: E402
import app.core.trading.vnpy_listeners as L  # noqa: E402


class _FakeCursor:
    """够用的内存 PG：paper_trades（台账）+ paper_positions（写回目标）。"""

    def __init__(self, trades, positions):
        self.trades = trades                # {symbol: [(direction, price, volume), ...]}
        self.positions = positions          # {symbol: [volume, avg_price]}
        self.statements = []                # 记录写语句，供断言
        self._result = []

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split())
        self.statements.append((s, params))
        if s.startswith("SELECT direction, price, volume FROM paper_trades"):
            self._result = list(self.trades.get(params[1], []))
        elif s.startswith("SELECT volume, avg_price FROM paper_positions"):
            row = self.positions.get(params[0])
            self._result = [(row[0], row[1])] if row else []
        elif s.startswith("UPDATE paper_positions SET volume"):
            vol, avg, _now, symbol = params
            self.positions[symbol] = [vol, avg]
            self._result = []
        elif s.startswith("INSERT INTO paper_positions"):
            # 两种形态：护栏补建（symbol, today, avg, now, vol, avg）
            #            引擎旧路径（symbol, today, price, now, vol, frozen, price）
            symbol = params[0]
            vol = params[4]
            avg = params[2] if len(params) == 6 else params[6]
            self.positions[symbol] = [vol, avg]
            self._result = []
        elif s.startswith("DELETE FROM paper_positions"):
            self.positions.pop(params[0], None)
            self._result = []
        else:
            self._result = []
        return self

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        pass


def _run_sync(trades, positions, volume, symbol="SH588170"):
    """跑一次持仓事件写回，返回 (positions, cursor)。"""
    cur = _FakeCursor(trades, positions)
    conn = _FakeConn(cur)
    with mock.patch.object(L, "_get_conn", return_value=conn):
        L._sync_position({"host": "x"}, (symbol, volume, 0, 0.9087, "2026-09-19 15:05:00",
                                         "2026-09-19"))
    return positions, cur


class TestFifoPosition(unittest.TestCase):
    def test_none_when_no_trades(self):
        self.assertIsNone(PL.fifo_position([]))

    def test_none_when_only_foreign_vocab(self):
        self.assertIsNone(PL.fifo_position([("BUY_SOMETHING", 1.0, 100)]))

    def test_fifo_avg_and_net(self):
        rows = [("买入", 1.0, 1000), ("买入", 1.2, 1000), ("卖出", 1.5, 1500)]
        self.assertEqual(PL.fifo_position(rows), (500, 1.2))

    def test_english_direction_accepted(self):
        self.assertEqual(PL.fifo_position([("buy", 2.0, 300), ("sell", 3.0, 100)]), (200, 2.0))

    def test_oversell_flattens(self):
        self.assertEqual(PL.fifo_position([("买入", 1.0, 100), ("卖出", 1.1, 500)]), (0, 0.0))

    def test_real_case_588170_shape(self):
        # 09-18 10:36 播种态 8200 @0.9087439 → 之后台账还有 2400/1700/1200 的卖出没有回灌引擎
        rows = [("买入", 0.89, 35700), ("卖出", 1.005, 1200)]
        self.assertEqual(PL.fifo_position(rows), (34500, 0.89))


class TestGuardSwitch(unittest.TestCase):
    def test_default_on(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VNPY_POSITION_LEDGER_GUARD", None)
            self.assertTrue(PL.guard_enabled())

    def test_off(self):
        with mock.patch.dict(os.environ, {"VNPY_POSITION_LEDGER_GUARD": "0"}):
            self.assertFalse(PL.guard_enabled())


class TestSyncPositionGuard(unittest.TestCase):
    # 588170 真实台账尾段（09-14 起）：买 35700@0.89 + 买 5300@0.919，卖 38100 → 2900 股 @0.919
    LEDGER_588170 = [
        ("买入", 0.89, 35700), ("卖出", 0.889, 17800), ("买入", 0.919, 5300),
        ("卖出", 0.97, 7000), ("卖出", 0.972, 4600), ("卖出", 0.981, 3400),
        ("卖出", 0.997, 2400), ("卖出", 0.992, 1700), ("卖出", 1.005, 1200),
    ]

    def test_ledger_replay_matches_production_tail(self):
        vol, avg = PL.fifo_position(self.LEDGER_588170)
        self.assertEqual(vol, 2900)
        self.assertAlmostEqual(avg, 0.919, places=9)

    def test_ledger_wins_over_stale_engine_snapshot(self):
        """生产实况：引擎快照 7000（09-18 10:36 过期），台账 2900 ⇒ 写台账。"""
        trades = {"SH588170": self.LEDGER_588170}
        positions = {"SH588170": [7000, 0.9087439024390244]}
        positions, _cur = _run_sync(trades, positions, 7000)
        self.assertEqual(positions["SH588170"][0], 2900)
        self.assertAlmostEqual(positions["SH588170"][1], 0.919, places=9)

    def test_engine_snapshot_never_written_when_ledger_exists(self):
        trades = {"SH588170": self.LEDGER_588170}
        positions = {"SH588170": [7000, 0.9087439024390244]}
        _positions, cur = _run_sync(trades, positions, 7000)
        for sql, params in cur.statements:
            if sql.startswith("UPDATE paper_positions SET volume") or sql.startswith("INSERT INTO paper_positions"):
                self.assertNotIn(7000, params, f"引擎快照被写进了库: {sql} {params}")

    def test_idempotent_no_write_when_already_aligned(self):
        trades = {"SH588170": self.LEDGER_588170}
        positions = {"SH588170": [2900, 0.919]}
        _positions, cur = _run_sync(trades, positions, 7000)
        writes = [s for s, _p in cur.statements if s.startswith(("UPDATE paper_positions", "INSERT INTO paper_positions", "DELETE FROM paper_positions"))]
        self.assertEqual(writes, [], "与台账一致时不应反复写库（每 3 秒一次的写放大）")

    def test_ledger_flat_deletes_row(self):
        trades = {"SH588170": [("买入", 0.9, 1000), ("卖出", 0.95, 1000)]}
        positions = {"SH588170": [1000, 0.9]}
        positions, _cur = _run_sync(trades, positions, 1000)
        self.assertNotIn("SH588170", positions)

    def test_no_trades_falls_back_to_engine(self):
        """外部同步仓：台账无成交 ⇒ 保持旧行为（引擎口径）。"""
        positions = {}
        positions, cur = _run_sync({}, positions, 1234, symbol="SH999999")
        self.assertEqual(positions["SH999999"][0], 1234)

    def test_guard_can_be_disabled(self):
        trades = {"SH588170": self.LEDGER_588170}
        positions = {"SH588170": [7000, 0.9087439024390244]}
        with mock.patch.dict(os.environ, {"VNPY_POSITION_LEDGER_GUARD": "0"}):
            positions, _cur = _run_sync(trades, positions, 7000)
        self.assertEqual(positions["SH588170"][0], 7000, "开关注释：置 0 应回滚到旧行为")


class TestWiring(unittest.TestCase):
    def test_guard_is_consulted_before_engine_write(self):
        src = (REPO_ROOT / "backend/app/core/trading/vnpy_listeners.py").read_text(encoding="utf-8")
        guard_at = src.index("position_ledger.guard_enabled()")
        delete_at = src.index("if volume <= 0:\n            cur.execute(\n                \"DELETE FROM paper_positions")
        self.assertLess(guard_at, delete_at, "护栏必须在引擎口径写库（含删除分支）之前")

    def test_recon_reports_each_line_failure(self):
        src = (REPO_ROOT / "jobs/recon_account_cash.py").read_text(encoding="utf-8")
        self.assertIn("failures.append", src)
        self.assertIn("持仓差异", src)
        self.assertNotIn("❌ 漂移超阈值", src, "失败文案不应再把持仓差异误报成现金漂移")


if __name__ == "__main__":
    unittest.main()
