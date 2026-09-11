# -*- coding: utf-8 -*-
"""P2-4 收盘确认止损接线单测（2026-09-10）。

覆盖: t_monitor._stop_close_confirm —— 把既有纯函数 t_stop_loss_guard.evaluate_stop
接入生产止损路径时的**取数口径与降级行为**。

狼大依据: 2026-01-29「今天没跌破我没出, 我说了 收盘跌破我才出」;
         2026-01-12「等收盘确认破位出清」。
"""
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading", REPO_ROOT / "backend"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

PARAMS = {
    "stop_close_confirm": True,
    "stop_recovery_pct": 1.0,
    "stop_confirm_bars": 5,
    "stop_volume_filter": True,
    "stop_support_proximity_pct": 1.5,
}


def _bars(close, low, vol=1000.0, n=1):
    return [{"time": "2026-09-10 10:%02d:00" % (i * 5), "low": low, "close": close, "vol": vol}
            for i in range(n)]


class TestStopExitVolume(unittest.TestCase):
    """P2-4 完整落地: 收盘确认破位 → 清仓(含底仓); 盘中确认 → 减半仓。"""

    def setUp(self):
        from app.services.t_monitor import _stop_exit_volume, _in_close_window
        self.vol = _stop_exit_volume
        self.win = _in_close_window
        self._saved = os.environ.pop("WOLF_BASE_EXIT_CLOSE", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["WOLF_BASE_EXIT_CLOSE"] = self._saved
        else:
            os.environ.pop("WOLF_BASE_EXIT_CLOSE", None)

    def test_close_window_clears_all_including_floor(self):
        v, mode = self.vol(1000, True)
        self.assertEqual((v, mode), (1000, "close_clear"))

    def test_intraday_halves(self):
        v, mode = self.vol(1000, False)
        self.assertEqual((v, mode), (500, "half"))

    def test_intraday_odd_lot_floors_to_full(self):
        """100 股时减半=0 → 回退为全部(与旧实现一致: half<100 时取 s)。"""
        v, mode = self.vol(100, False)
        self.assertEqual((v, mode), (100, "half"))

    def test_close_window_disabled_falls_back_to_half(self):
        os.environ["WOLF_BASE_EXIT_CLOSE"] = "0"
        v, mode = self.vol(1000, True)
        self.assertEqual((v, mode), (500, "half"))

    def test_zero_or_invalid_sellable(self):
        self.assertEqual(self.vol(0, True)[0], 0)
        self.assertEqual(self.vol(None, True)[0], 0)

    def test_close_window_boundary(self):
        import datetime as dt
        self.assertFalse(self.win(dt.datetime(2026, 9, 10, 14, 54)))
        self.assertTrue(self.win(dt.datetime(2026, 9, 10, 14, 55)))
        self.assertTrue(self.win(dt.datetime(2026, 9, 10, 15, 0)))


class TestStopCloseConfirm(unittest.TestCase):
    def setUp(self):
        from app.services.t_monitor import _stop_close_confirm
        self.fn = _stop_close_confirm

    def test_tick_pierce_but_bar_close_recovers_holds(self):
        """tick 插针(现价破位) 但 5min bar 收盘收回 ≥1% → 未确认, 不执行止损。"""
        r = self.fn(9.95, 10.0, _bars(10.2, 9.9), None, PARAMS)
        self.assertIsNotNone(r)
        self.assertEqual(r["action"], "hold", r)
        self.assertIn("收回幅度", r["reason"])

    def test_bar_close_below_stop_confirms(self):
        """5min bar 收盘 ≤ 止损价 且未缩量贴支撑 → 确认破位, 执行。"""
        r = self.fn(9.85, 10.0, _bars(9.8, 9.9, vol=10000.0), None, PARAMS)
        self.assertIsNotNone(r)
        self.assertEqual(r["action"], "stop", r)
        self.assertIn("收盘确认破位", r["reason"])

    def test_no_m5_data_returns_none(self):
        """无 m5 数据 → 返回 None(不做限制), 调用方按原口径执行 —— 避免因缺数据漏止损。"""
        self.assertIsNone(self.fn(9.9, 10.0, [], None, PARAMS))
        self.assertIsNone(self.fn(9.9, 10.0, None, None, PARAMS))

    def test_guard_exception_returns_none(self):
        """守卫异常 → 返回 None(降级到原口径), 不抛给调用方。"""
        bad = [{"time": "x", "low": object(), "close": object(), "vol": object()}]
        self.assertIsNone(self.fn(9.9, 10.0, bad, None, PARAMS))

    def test_stop_close_confirm_disabled_in_params(self):
        """params 里关掉收盘确认 → 直接 stop(原盘中触发口径)。"""
        p = dict(PARAMS, stop_close_confirm=False)
        r = self.fn(9.95, 10.0, _bars(10.2, 9.9), None, p)
        self.assertEqual(r["action"], "stop", r)


if __name__ == "__main__":
    unittest.main()
