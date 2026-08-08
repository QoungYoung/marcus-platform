# -*- coding: utf-8 -*-
"""黄金坑纯计算函数 — 分位、趋势、状态、退出、入坑日检测与交易日数学。"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.services.golden_pit_config import (
    FAKE_SIGNAL_REBOUND_DAYS,
    PERCENTILE_GOLDEN_PIT,
    PERCENTILE_WINDOW_DAYS,
    PERCENTILE_WARNING,
    TURNING_CONSECUTIVE_DAYS,
)

def _trading_days_between(start_date: str, end_date: str) -> int:
    """估算两个日期之间的交易日数（简化为自然日 * 5/7）。"""
    try:
        d1 = datetime.strptime(start_date, "%Y-%m-%d")
        d2 = datetime.strptime(end_date, "%Y-%m-%d")
        days = (d2 - d1).days
        # 粗略估算交易日：自然日 * 5/7
        return max(0, round(days * 5 / 7))
    except (ValueError, TypeError):
        return 0


def _add_trading_days(date_str: str, trading_days: int) -> str:
    """给定起始日期和交易日数，估算目标日期。"""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        cal_days = round(trading_days * 7 / 5)
        result = d + timedelta(days=cal_days)
        return result.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return date_str


def _calculate_price_percentile(current_price: float, closes: List[float]) -> float:
    """计算当前价格在滚动窗口中的分位数 (0-100)。

    分位越低表示价格越接近区间低点（越恐慌）。
    """
    if not closes or len(closes) < 5:
        return 50.0
    count_below = sum(1 for c in closes if c < current_price)
    return round(count_below / len(closes) * 100, 1)


def _price_based_greed(current_price: float, closes: List[float]) -> float:
    """从价格位置合成 greed 代理值 (0-1 尺度)。

    0 = 处于滚动窗口最低价（极端恐慌/黄金坑）
    1 = 处于滚动窗口最高价（极端贪婪）
    """
    if not closes or len(closes) < 5:
        return 0.50
    min_p = min(closes)
    max_p = max(closes)
    if max_p <= min_p:
        return 0.50
    return round((current_price - min_p) / (max_p - min_p), 4)


def _price_decline_rate(closes: List[float], window: int = 5) -> float:
    """从价格计算 N 日平均跌幅（正值=下跌）。"""
    if len(closes) < window + 1:
        return 0.0
    recent = closes[-window - 1:]
    if len(recent) < 2:
        return 0.0
    total_decline = recent[0] - recent[-1]
    return round(total_decline / recent[0] / window, 4) if recent[0] != 0 else 0.0

# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════


def _detect_p10_entry(
    sorted_series: List[Dict], today_str: str,
    entry_pct: int = PERCENTILE_WARNING,
    fixed_threshold: Optional[float] = None,
) -> tuple:
    """检测当前是否在预警信号中，以及 Day 1 是哪天。

    当 fixed_threshold 不为 None 时使用固定贪婪阈值 (回测最优),
    否则使用滚动窗口百分位阈值。

    Returns:
        (p10_entry_date, days_in_warning, is_first_cross)
    """
    greeds = [float(s.get("greed", 0)) for s in sorted_series]
    dates = [s.get("date", "") for s in sorted_series]

    if len(greeds) < 60:
        return (None, 0, False)

    if fixed_threshold is not None:
        entry_threshold = fixed_threshold
    else:
        window_greeds = greeds[-PERCENTILE_WINDOW_DAYS:] if len(greeds) > PERCENTILE_WINDOW_DAYS else greeds
        all_sorted = sorted(window_greeds)
        threshold_idx = int(len(all_sorted) * entry_pct / 100)
        entry_threshold = all_sorted[min(threshold_idx, len(all_sorted) - 1)]
    if greeds[-1] > entry_threshold:
        return (None, 0, False)

    # 往回找到最近一次贪婪值高于阈值的位置，其后一天就是 Day 1
    entry_idx = 0  # 默认：全部历史数据都在预警区内
    for i in range(len(greeds) - 1, -1, -1):
        if greeds[i] > entry_threshold:
            entry_idx = i + 1
            break

    if entry_idx >= len(greeds):
        return (None, 0, False)

    p10_entry_date = dates[entry_idx]
    days_in = _trading_days_between(p10_entry_date, today_str) + 1
    is_first_cross = days_in <= FAKE_SIGNAL_REBOUND_DAYS + 1

    return (p10_entry_date, days_in, is_first_cross)


def _calculate_percentile(current_greed: float, series: List[Dict], window: int = None) -> float:
    """计算当前贪婪值在自身历史中的分位数（越低越恐慌）。

    使用滚动窗口而非 expanding-window：窗口大小恒定 (默认500天)，
    Px 对应的贪婪阈值不会随数据累积而漂移。
    """
    if window is None:
        window = PERCENTILE_WINDOW_DAYS
    if not series:
        return 50.0
    # 只取最近 window 天，避免 expanding-window 漂移
    window_series = series[-window:] if len(series) > window else series
    greeds = sorted([float(s.get("greed", 0)) for s in window_series])
    if not greeds or len(greeds) < 2:
        return 50.0
    count_below = sum(1 for g in greeds if g < current_greed)
    return round(count_below / len(greeds) * 100, 1)


def _calculate_decline_rate(series: List[Dict], window: int = 5) -> float:
    """计算最近 N 日的平均贪婪值日跌幅（正值=下跌，负值=上涨）。"""
    if len(series) < window + 1:
        return 0.0
    recent = sorted(series, key=lambda x: x.get("date", ""))[-window - 1:]
    greeds = [float(s.get("greed", 0)) for s in recent]
    if len(greeds) < 2:
        return 0.0
    total_decline = greeds[0] - greeds[-1]
    return round(total_decline / window, 4)


def _determine_status(cfg: Dict[str, Any], greed: float, percentile: float) -> str:
    """判定指数状态: 优先使用固定贪婪阈值 (回测最优), 其次使用滚动百分位。

    use_fixed_greed=True 时用 pit_greed/entry_greed 固定值比较,
    消除 expanding-window percentile 的 Px 漂移问题。
    """
    if cfg.get("use_fixed_greed"):
        pit_greed = cfg.get("pit_greed")
        entry_greed = cfg.get("entry_greed")
        if pit_greed is not None and greed <= pit_greed:
            return "golden_pit"
        elif entry_greed is not None and greed <= entry_greed:
            return "warning"
        return "normal"
    else:
        pit_pct = cfg.get("pit_pct", PERCENTILE_GOLDEN_PIT)
        entry_pct = cfg.get("entry_pct", PERCENTILE_WARNING)
        if percentile <= pit_pct:
            return "golden_pit"
        elif percentile <= entry_pct:
            return "warning"
        return "normal"


def _detect_trend(sorted_series: List[Dict], turning_days: int = None) -> Dict[str, Any]:
    """检测贪婪值趋势方向，判断是否已过拐点。

    拐点 = 贪婪值从连续下降转为连续回升。连续 N 天回升确认拐点。

    Returns:
        trend: "declining" | "bottoming" | "recovering"
        days_rising: 连续回升天数
        turning_confirmed: 是否已确认拐点
    """
    if turning_days is None:
        turning_days = TURNING_CONSECUTIVE_DAYS

    if len(sorted_series) < 5:
        return {"trend": "declining", "days_rising": 0,
                "turning_confirmed": False, "last_change": 0.0}

    greeds = [float(s.get("greed", 0)) for s in sorted_series]

    days_rising = 0
    for i in range(len(greeds) - 1, 0, -1):
        if greeds[i] > greeds[i - 1]:
            days_rising += 1
        else:
            break

    last_change = round(greeds[-1] - greeds[-2], 4) if len(greeds) >= 2 else 0.0

    if days_rising >= turning_days:
        return {"trend": "recovering", "days_rising": days_rising,
                "turning_confirmed": True, "last_change": last_change}
    elif days_rising == turning_days - 1 and turning_days >= 2:
        return {"trend": "bottoming", "days_rising": days_rising,
                "turning_confirmed": False, "last_change": last_change}
    elif days_rising >= 1 and turning_days == 1:
        return {"trend": "recovering", "days_rising": days_rising,
                "turning_confirmed": True, "last_change": last_change}
    else:
        return {"trend": "declining", "days_rising": 0,
                "turning_confirmed": False, "last_change": last_change}


def _detect_exit_signal(
    sorted_series: List[Dict],
    turning_confirmed: bool,
    percentile: float,
    exit_full_pct: int = 50,
    exit_half_pct: int = 30,
) -> Dict[str, Any]:
    """检测退出信号（全量回测校准 per-index 参数）。

    只在拐点确认后才发出退出信号（拐点前不退出）。
    退出规则:
      - percentile >= exit_full_pct → full_exit (清仓)
      - percentile >= exit_half_pct → half_exit (卖一半)
      - 拐点后连续2天回落且曾回到 exit_half_pct → stop_profit (止盈保护)

    Returns:
        {signal: null|"half_exit"|"full_exit"|"stop_profit", reason: str}
    """
    result = {"signal": None, "reason": ""}

    if not turning_confirmed:
        return result

    if len(sorted_series) < 5:
        return result

    greeds = [float(s.get("greed", 0)) for s in sorted_series]

    # 全清退出 (per-index threshold)
    if percentile >= exit_full_pct:
        result["signal"] = "full_exit"
        result["reason"] = f"贪婪值回升至 P{percentile:.0f}≥P{exit_full_pct}，建议清仓"
        return result

    # 减半退出 (per-index threshold)
    if percentile >= exit_half_pct:
        result["signal"] = "half_exit"
        result["reason"] = f"贪婪值回升至 P{percentile:.0f}≥P{exit_half_pct}，建议减持 50%"
        return result

    # 拐点后连续回落 → 止盈保护
    days_declining = 0
    for i in range(len(greeds) - 1, 0, -1):
        if greeds[i] < greeds[i - 1]:
            days_declining += 1
        else:
            break
    if days_declining >= 2:
        max_greed = max(greeds[-10:]) if len(greeds) >= 10 else max(greeds)
        all_vals = sorted(greeds)
        max_pct = sum(1 for g in all_vals if g <= max_greed) / len(all_vals) * 100
        if max_pct >= exit_half_pct:
            result["signal"] = "stop_profit"
            result["reason"] = (
                f"拐点后连续{days_declining}天回落（曾回升至P{max_pct:.0f}≥P{exit_half_pct}），建议止盈"
            )
            return result

    return result
