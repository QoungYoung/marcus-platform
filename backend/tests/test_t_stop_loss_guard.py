# -*- coding: utf-8 -*-
"""假跌破守卫单元测试（add-fake-breakdown-stop-guard，覆盖 spec 全部场景）。"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "core", REPO_ROOT / "apps" / "paper-trading"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services.t_stop_loss_guard import evaluate_stop

DEFAULT_PARAMS = {
    "stop_close_confirm": True,
    "stop_recovery_pct": 1.0,
    "stop_confirm_bars": 5,
    "stop_volume_filter": True,
    "stop_support_proximity_pct": 1.5,
}


def _bar(low, close, vol=1000.0, time="2026-08-07 10:00:00"):
    return {"time": time, "open": close, "high": max(low, close), "low": low,
            "close": close, "vol": vol}


def _day_m5(n=10, vol=1000.0):
    return [_bar(5.0, 5.3, vol=vol, time=f"2026-08-07 09:{i:02d}:00") for i in range(30, 30 + n)]


class TestStopLossGuard(unittest.TestCase):
    def test_close_confirm_executes_stop(self):
        """收盘确认：收盘 ≤ 止损价 → 执行止损。"""
        bar = _bar(5.18, 5.19)  # low 破 5.20，close ≤ 5.20
        r = evaluate_stop(bar, _day_m5(), 5.20, DEFAULT_PARAMS)
        self.assertEqual(r["action"], "stop")
        self.assertIn("收盘确认", r["reason"])

    def test_wick_recovered_is_fake_breakdown(self):
        """下影线插针但收盘收回 ≥1% → 假跌破，跳过并重置基准为收盘价。"""
        bar = _bar(5.18, 5.30)  # 收回 (5.30-5.20)/5.20 = 1.92% ≥ 1%
        r = evaluate_stop(bar, _day_m5(), 5.20, DEFAULT_PARAMS)
        self.assertEqual(r["action"], "hold")
        self.assertIn("假跌破", r["reason"])
        self.assertEqual(r["reset_stop"], 5.30)

    def test_wick_close_above_stop_but_small_recovery_holds(self):
        """收回幅度不足（<1%）且无企稳/缩量 → hold（收盘未确认），不重置。"""
        bar = _bar(5.18, 5.21)  # 收回 0.19%
        r = evaluate_stop(bar, _day_m5(), 5.20, DEFAULT_PARAMS)
        self.assertEqual(r["action"], "hold")
        self.assertIn("未确认", r["reason"])
        self.assertIsNone(r["reset_stop"])

    def test_stabilised_cancel(self):
        """分钟企稳：触发时刻前连续 N 根 1min 收盘高于止损 → 企稳取消（hold+reset）。"""
        bar = _bar(5.18, 5.22, time="2026-08-07 10:00:00")
        m1 = [{"time": f"2026-08-07 09:5{i}:00", "close": 5.25} for i in range(6)]
        r = evaluate_stop(bar, _day_m5(), 5.20, DEFAULT_PARAMS, m1_today=m1)
        self.assertEqual(r["action"], "hold")
        self.assertIn("企稳", r["reason"])
        self.assertEqual(r["reset_stop"], 5.22)

    def test_volume_shrink_near_support_blocks_close_break(self):
        """收盘破位但缩量且贴支撑 → hold（疑似洗盘），重置基准。"""
        bar = _bar(5.18, 5.19, vol=100.0)  # 缩量（均量1000）
        daily = [{"trade_date": "20260806", "low": 5.21},
                  {"trade_date": "20260807", "low": 5.35}]  # 前低贴近止损 5.20（末日视为当日排除）
        r = evaluate_stop(bar, _day_m5(vol=1000.0), 5.20, DEFAULT_PARAMS, daily_bars=daily)
        self.assertEqual(r["action"], "hold")
        self.assertIn("缩量", r["reason"])
        self.assertEqual(r["reset_stop"], 5.19)

    def test_support_proximity_lowers_recovery_threshold(self):
        """支撑位附近：收回 0.6%（≥0.5）即判假跌破。"""
        bar = _bar(5.18, 5.23)  # 收回 0.58%
        daily = [{"trade_date": "20260806", "low": 5.21},
                  {"trade_date": "20260807", "low": 5.35}]
        r = evaluate_stop(bar, _day_m5(), 5.20, DEFAULT_PARAMS, daily_bars=daily)
        self.assertEqual(r["action"], "hold")
        self.assertIn("假跌破", r["reason"])

    def test_param_close_confirm_off_restores_legacy(self):
        """stop_close_confirm=False → 恢复原盘中触发口径（直接 stop）。"""
        bar = _bar(5.18, 5.30)  # 即使收盘收回也立即止损（旧行为）
        params = dict(DEFAULT_PARAMS, stop_close_confirm=False)
        r = evaluate_stop(bar, _day_m5(), 5.20, params)
        self.assertEqual(r["action"], "stop")

    def test_param_recovery_threshold_override(self):
        """stop_recovery_pct=2.0：收回 1.5% 不再算假跌破（hold 未确认）。"""
        bar = _bar(5.18, 5.28)  # 收回 1.54%
        params = dict(DEFAULT_PARAMS, stop_recovery_pct=2.0)
        r = evaluate_stop(bar, _day_m5(), 5.20, params)
        self.assertEqual(r["action"], "hold")
        self.assertNotIn("假跌破", r["reason"])


if __name__ == "__main__":
    unittest.main()
