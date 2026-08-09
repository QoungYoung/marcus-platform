# -*- coding: utf-8 -*-
"""黄金坑纯计算函数首批单测。

覆盖 golden_pit_indicators 的 percentile/trend/status/exit 及交易日数学，
并覆盖重构变更 1.1 (prev_greed)、1.3 (ETA 基准)、1.4 (get_score 可交易过滤) 的修复场景。
运行: backend/venv/Scripts/python.exe -m unittest tests.test_golden_pit_indicators -v
"""
import unittest
from datetime import datetime, timedelta
from unittest import mock

from app.services.golden_pit_indicators import (
    _add_trading_days,
    _calculate_percentile,
    _calculate_price_percentile,
    _detect_exit_signal,
    _detect_p10_entry,
    _detect_trend,
    _determine_status,
    _price_based_greed,
    _price_decline_rate,
    _trading_days_between,
)
from app.services.golden_pit_config import (
    CHINA_INDICES,
    PERCENTILE_GOLDEN_PIT,
    PERCENTILE_WARNING,
)
from app.services.golden_pit_service import GoldenPitService


def make_series(values, start="2025-10-01"):
    """按贪婪值序列生成 {date, greed, close} 序列，日期逐日递增。"""
    d = datetime.strptime(start, "%Y-%m-%d")
    out = []
    for v in values:
        out.append({"date": d.strftime("%Y-%m-%d"), "greed": v, "close": 1.0})
        d += timedelta(days=1)
    return out


class TestPercentile(unittest.TestCase):
    def test_empty_series_falls_back_to_50(self):
        self.assertEqual(_calculate_percentile(0.3, []), 50.0)
        self.assertEqual(_calculate_price_percentile(10.0, []), 50.0)

    def test_min_value_is_zero_percentile(self):
        series = make_series([0.8, 0.6, 0.4, 0.2])
        self.assertEqual(_calculate_percentile(0.2, series), 0.0)

    def test_max_value_is_top_percentile(self):
        # 严格小于计数: max 值 = (n-1)/n
        series = make_series([0.8, 0.6, 0.4, 0.2])
        self.assertEqual(_calculate_percentile(0.8, series), 75.0)

    def test_rolling_window_uses_only_last_window_days(self):
        # window=3 时只取最后 3 天 [0.1, 0.2, 0.9]
        series = make_series([0.5, 0.6, 0.7, 0.1, 0.2, 0.9])
        # 在 [0.1, 0.2, 0.9] 中，0.3 位于 2/3 -> 66.7
        self.assertEqual(_calculate_percentile(0.3, series, window=3), 66.7)

    def test_price_percentile_low_and_high(self):
        closes = [10.0, 11.0, 12.0, 13.0, 14.0]
        self.assertEqual(_calculate_price_percentile(10.0, closes), 0.0)
        self.assertEqual(_calculate_price_percentile(14.0, closes), 80.0)


class TestPriceGreedAndDecline(unittest.TestCase):
    def test_price_based_greed_bounds(self):
        closes = [10.0, 11.0, 12.0, 13.0, 14.0]
        self.assertEqual(_price_based_greed(10.0, closes), 0.0)
        self.assertEqual(_price_based_greed(14.0, closes), 1.0)
        self.assertEqual(_price_based_greed(12.0, closes), 0.5)

    def test_price_based_greed_flat_or_short(self):
        self.assertEqual(_price_based_greed(10.0, [10.0, 10.0, 10.0]), 0.5)
        self.assertEqual(_price_based_greed(10.0, [10.0, 11.0]), 0.5)

    def test_price_decline_rate_positive_on_fall(self):
        # 5 日跌幅: (100-80)/100/5 = 0.04
        closes = [100.0, 96.0, 92.0, 88.0, 84.0, 80.0]
        self.assertEqual(_price_decline_rate(closes, window=5), 0.04)

    def test_price_decline_rate_insufficient_data(self):
        self.assertEqual(_price_decline_rate([1.0, 2.0]), 0.0)


class TestDetermineStatus(unittest.TestCase):
    """覆盖 1.3 的固定阈值 vs 滚动百分位基准一致性。"""

    def test_fixed_greed_thresholds(self):
        cfg = {"use_fixed_greed": True, "pit_greed": 0.35, "entry_greed": 0.40}
        self.assertEqual(_determine_status(cfg, 0.30, 99.0), "golden_pit")
        self.assertEqual(_determine_status(cfg, 0.37, 99.0), "warning")
        self.assertEqual(_determine_status(cfg, 0.50, 99.0), "normal")

    def test_percentile_thresholds(self):
        cfg = {"use_fixed_greed": False}
        self.assertEqual(_determine_status(cfg, 0.9, 3.0), "golden_pit")
        self.assertEqual(_determine_status(cfg, 0.9, 8.0), "warning")
        self.assertEqual(_determine_status(cfg, 0.9, 50.0), "normal")

    def test_percentile_defaults_use_config_constants(self):
        self.assertEqual(PERCENTILE_GOLDEN_PIT, 5)
        self.assertEqual(PERCENTILE_WARNING, 10)


class TestDetectTrend(unittest.TestCase):
    def test_recovering_confirmed_after_two_rises(self):
        series = make_series([0.5, 0.4, 0.3, 0.31, 0.33])
        res = _detect_trend(series)
        self.assertEqual(res["trend"], "recovering")
        self.assertTrue(res["turning_confirmed"])
        self.assertEqual(res["days_rising"], 2)
        self.assertAlmostEqual(res["last_change"], 0.02)

    def test_bottoming_before_confirmation(self):
        series = make_series([0.6, 0.5, 0.4, 0.3, 0.31])
        res = _detect_trend(series)
        self.assertEqual(res["trend"], "bottoming")
        self.assertFalse(res["turning_confirmed"])
        self.assertEqual(res["days_rising"], 1)

    def test_declining_when_no_rise(self):
        series = make_series([0.5, 0.4, 0.3, 0.2, 0.1])
        res = _detect_trend(series)
        self.assertEqual(res["trend"], "declining")
        self.assertFalse(res["turning_confirmed"])
        self.assertEqual(res["days_rising"], 0)

    def test_short_series_declining(self):
        res = _detect_trend(make_series([0.5, 0.4]))
        self.assertEqual(res["trend"], "declining")


class TestDetectExitSignal(unittest.TestCase):
    def test_no_signal_before_turning_confirmed(self):
        res = _detect_exit_signal(make_series([0.5, 0.4, 0.3]), False, 90.0)
        self.assertIsNone(res["signal"])

    def test_full_exit_at_high_percentile(self):
        res = _detect_exit_signal(make_series([0.5, 0.4, 0.3, 0.31, 0.33]), True, 85.0, exit_full_pct=80, exit_half_pct=40)
        self.assertEqual(res["signal"], "full_exit")

    def test_half_exit(self):
        res = _detect_exit_signal(make_series([0.5, 0.4, 0.3, 0.31, 0.33]), True, 45.0, exit_full_pct=80, exit_half_pct=40)
        self.assertEqual(res["signal"], "half_exit")

    def test_stop_profit_after_two_declines(self):
        # 曾回升（0.31 -> 0.33）且 max_greed 达 P>=40，随后连续两天回落
        series = make_series([0.20, 0.21, 0.22, 0.31, 0.33, 0.32, 0.30])
        res = _detect_exit_signal(series, True, 20.0, exit_full_pct=80, exit_half_pct=40)
        self.assertEqual(res["signal"], "stop_profit")

    def test_no_exit_when_below_half(self):
        series = make_series([0.20, 0.21, 0.22, 0.31, 0.33, 0.34])
        res = _detect_exit_signal(series, True, 10.0, exit_full_pct=80, exit_half_pct=40)
        self.assertIsNone(res["signal"])

    def test_down_turn_exit_after_turn(self):
        # 已确认过拐点(0.33)后连续 3 天回落 → 二次拐点向下清仓
        series = make_series([0.20, 0.21, 0.22, 0.31, 0.33, 0.32, 0.30, 0.28])
        res = _detect_exit_signal(series, False, 10.0, exit_full_pct=80, exit_half_pct=40,
                                  exit_down_days=3, turn_started=True)
        self.assertEqual(res["signal"], "full_exit")
        self.assertIn("二次拐点", res["reason"])

    def test_down_turn_no_exit_before_first_turn(self):
        # 尚未确认过拐点(入场前的初始下跌) → 不触发二次拐点离场
        series = make_series([0.50, 0.45, 0.40, 0.35, 0.30])
        res = _detect_exit_signal(series, False, 10.0, exit_full_pct=80, exit_half_pct=40,
                                  exit_down_days=3, turn_started=False)
        self.assertIsNone(res["signal"])

    def test_down_turn_short_decline_no_exit(self):
        # 仅连续 2 天回落，不足 3 天 → 不触发
        series = make_series([0.20, 0.21, 0.22, 0.31, 0.33, 0.32, 0.30])
        res = _detect_exit_signal(series, False, 10.0, exit_full_pct=80, exit_half_pct=40,
                                  exit_down_days=3, turn_started=True)
        self.assertIsNone(res["signal"])

    def test_down_turn_disabled_by_default(self):
        # 未配置 exit_down_days → 走原有 P 分位退出逻辑，不受影响
        series = make_series([0.50, 0.45, 0.40, 0.35, 0.30])
        res = _detect_exit_signal(series, False, 10.0, exit_full_pct=80, exit_half_pct=40)
        self.assertIsNone(res["signal"])


class TestDetectP10Entry(unittest.TestCase):
    def test_fixed_threshold_crossing(self):
        series = make_series([0.5] * 59 + [0.30])
        p10_date, days_in, is_first = _detect_p10_entry(series, series[-1]["date"], fixed_threshold=0.35)
        self.assertEqual(p10_date, series[-1]["date"])
        self.assertEqual(days_in, 1)
        self.assertTrue(is_first)

    def test_fixed_threshold_not_crossed(self):
        series = make_series([0.5] * 59 + [0.30])
        p10_date, days_in, is_first = _detect_p10_entry(series, series[-1]["date"], fixed_threshold=0.25)
        self.assertIsNone(p10_date)
        self.assertEqual(days_in, 0)
        self.assertFalse(is_first)

    def test_rolling_threshold_path(self):
        # 无 fixed_threshold 时使用滚动窗口 P(entry_pct): 前58天0.7、倒数第2天0.8、最后1天0.30
        series = make_series([0.7] * 58 + [0.8, 0.30])
        p10_date, days_in, _ = _detect_p10_entry(series, series[-1]["date"])
        self.assertEqual(p10_date, series[-1]["date"])
        self.assertEqual(days_in, 1)

    def test_short_series_returns_none(self):
        p10_date, days_in, is_first = _detect_p10_entry(make_series([0.5] * 30), "2025-12-01")
        self.assertIsNone(p10_date)
        self.assertEqual(days_in, 0)
        self.assertFalse(is_first)


class TestTradingDayMath(unittest.TestCase):
    def test_trading_days_between_approx(self):
        self.assertEqual(_trading_days_between("2026-01-01", "2026-01-08"), 5)
        self.assertEqual(_trading_days_between("2026-01-08", "2026-01-01"), 0)

    def test_add_trading_days_roundtrip(self):
        self.assertEqual(_add_trading_days("2026-01-01", 5), "2026-01-08")

    def test_invalid_dates_fallback(self):
        self.assertEqual(_trading_days_between("bad", "2026-01-08"), 0)
        self.assertEqual(_add_trading_days("bad", 5), "bad")


class TestBuildIndexInfoRegressions(unittest.TestCase):
    """1.1 prev_greed 取 sorted_series[-2]，不再被重复键覆盖。"""

    @classmethod
    def setUpClass(cls):
        cls.svc = GoldenPitService()

    def _build(self, cfg_key, value, status, decline_rate, series, as_of="2026-01-10"):
        cfg = CHINA_INDICES[cfg_key]
        return self.svc._build_index_info(
            code=cfg_key, cfg=cfg, value=value, close=1.0,
            percentile=8.0, decline_rate=decline_rate, status=status,
            absolute_triggered=False, data_source="test",
            sorted_series=series, as_of=as_of,
        )

    def test_prev_greed_from_second_last(self):
        series = make_series([0.5] * 68 + [0.42, 0.50])
        info = self._build("588000", value=0.50, status="normal", decline_rate=0.0, series=series)
        self.assertEqual(info["prev_greed"], round(0.42, 4))
        self.assertEqual(info["greed"], round(0.50, 4))

    def test_eta_uses_fixed_pit_greed(self):
        # use_fixed_greed=True: gap = value - pit_greed(0.348)
        series = make_series([0.5] * 69 + [0.40])
        info = self._build("588000", value=0.40, status="warning", decline_rate=0.01, series=series)
        self.assertEqual(info["days_to_pit"], 5)  # round(0.052 / 0.01)
        self.assertEqual(info["eta_date"], _add_trading_days("2026-01-10", 5))

    def test_eta_uses_rolling_pit_percentile(self):
        # use_fixed_greed=False (513400, pit_pct=3): 窗口 P3 ≈ 0.30
        series = make_series([0.30] * 69 + [0.40])
        info = self._build("513400", value=0.40, status="warning", decline_rate=0.01, series=series)
        self.assertEqual(info["days_to_pit"], 10)  # round(0.10 / 0.01)
        self.assertEqual(info["eta_date"], _add_trading_days("2026-01-10", 10))


class TestGetScoreTradeableOnly(unittest.TestCase):
    """1.4 get_score 只统计 core/satellite/defense，排除 drop/watch。"""

    @classmethod
    def setUpClass(cls):
        cls.svc = GoldenPitService()

    def _status(self, indices):
        return {"as_of": "2026-01-10", "indices": indices, "summary": "x"}

    def test_drop_and_watch_excluded(self):
        indices = [
            {"tier": "core", "status": "golden_pit", "percentile": 4.0, "signal_quality": "strong", "absolute_triggered": True},
            {"tier": "satellite", "status": "golden_pit", "percentile": 6.0, "signal_quality": "good", "absolute_triggered": False},
            {"tier": "defense", "status": "warning", "percentile": 9.0, "signal_quality": "good", "absolute_triggered": False},
            {"tier": "drop", "status": "golden_pit", "percentile": 1.0, "signal_quality": "strong", "absolute_triggered": True},
            {"tier": "watch", "status": "warning", "percentile": 2.0, "signal_quality": "strong", "absolute_triggered": True},
        ]
        with mock.patch.object(self.svc, "get_status", return_value=self._status(indices)):
            score = self.svc.get_score()
        self.assertEqual(score["level"], "golden_pit")
        self.assertEqual(score["score"], round(100 - 4.0, 1))
        # drop/watch 的黄金坑不计入 pit_count / double_confirmed
        self.assertIn("1个双重确认", score["level_label"])

    def test_only_drop_pit_means_normal(self):
        indices = [
            {"tier": "drop", "status": "golden_pit", "percentile": 1.0, "signal_quality": "strong", "absolute_triggered": True},
            {"tier": "watch", "status": "warning", "percentile": 2.0, "signal_quality": "strong", "absolute_triggered": True},
        ]
        with mock.patch.object(self.svc, "get_status", return_value=self._status(indices)):
            score = self.svc.get_score()
        self.assertEqual(score["level"], "normal")
        self.assertEqual(score["score"], 50.0)  # tradeable 为空, min_pct 默认 50.0

    def test_empty_tradeable_falls_back_to_50(self):
        with mock.patch.object(self.svc, "get_status", return_value=self._status([])):
            score = self.svc.get_score()
        self.assertEqual(score["level"], "normal")
        self.assertEqual(score["score"], 50.0)


if __name__ == "__main__":
    unittest.main()
