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


class TestStopTimeGate(unittest.TestCase):
    """④ 止损时点约束: 狼大 2026-03-23「止损绝对不应该是下午1点到2点半…要么早上卖要么尾盘卖」。"""

    def setUp(self):
        from app.services.t_monitor import _stop_time_ok
        self.fn = _stop_time_ok
        self._saved = {k: os.environ.pop(k, None)
                       for k in ("WOLF_STOP_TIME_GATE", "WOLF_STOP_BLOCK_FROM", "WOLF_STOP_BLOCK_TO")}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @staticmethod
    def _at(h, m):
        import datetime as dt
        return dt.datetime(2026, 9, 10, h, m)

    def test_blocked_window_13_00_to_14_30(self):
        for h, m in ((13, 0), (13, 30), (14, 0), (14, 29)):
            ok, reason = self.fn(self._at(h, m))
            self.assertFalse(ok, "%02d:%02d 应被拦" % (h, m))
            self.assertIn("2026-03-23", reason)

    def test_allowed_outside_window(self):
        for h, m in ((9, 30), (9, 59), (10, 0), (11, 30), (12, 59), (14, 30), (14, 45), (14, 55), (15, 0)):
            ok, _ = self.fn(self._at(h, m))
            self.assertTrue(ok, "%02d:%02d 应放行" % (h, m))

    def test_close_window_not_affected(self):
        """收盘清仓时点(默认>=14:55)必须不受时点门影响。"""
        from app.services.t_monitor import _in_close_window
        self.assertTrue(_in_close_window(self._at(14, 55)))
        self.assertTrue(self.fn(self._at(14, 55))[0])

    def test_gate_can_be_disabled(self):
        os.environ["WOLF_STOP_TIME_GATE"] = "0"
        self.assertTrue(self.fn(self._at(13, 30))[0])

    def test_custom_block_window(self):
        os.environ["WOLF_STOP_BLOCK_FROM"] = "1000"
        os.environ["WOLF_STOP_BLOCK_TO"] = "1100"
        self.assertFalse(self.fn(self._at(10, 30))[0])
        self.assertTrue(self.fn(self._at(13, 30))[0])


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
