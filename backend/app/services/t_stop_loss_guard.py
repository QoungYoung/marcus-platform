# -*- coding: utf-8 -*-
"""做T止损假跌破守卫（add-fake-breakdown-stop-guard）。

回测与实盘共用同一纯函数 evaluate_stop：在"盘中最低价 ≤ 止损价"候选触发时判定
是真跌破还是假跌破（下影线插针 / 缩量破位 / 支撑位附近 / 分钟企稳收回），
避免底仓被单根下影线洗出（#71 回测：13 次止损 9 次卖飞，利欧 +16.7%、金山 +14.1%）。

判定链（全部只用截至当前 tick 的历史数据，防前视）：
1. 收盘确认：收盘价 ≤ 止损价 → 真跌破（action=stop）；否则进入假跌破判定
2. 收回幅度：收盘相对止损价收回 ≥ stop_recovery_pct → 假跌破（hold + reset_stop=收盘价）
3. 分钟企稳：触发时刻前连续 stop_confirm_bars 根 1min 收盘 > 止损价 → 假跌破（hold + reset）
4. 缩量过滤：触发 bar 成交量 < 近 N 日均量 × 0.7 → 疑似洗盘，要求更强确认
5. 支撑位感知：止损价贴近前期低点 / 筹码成本峰（cyq_perf.cost_50pct）→ 更容易判假跌破
"""
from typing import Any, Dict, List, Optional


def _avg_vol(day_m5: List[dict], lookback: int = 20) -> float:
    vols = [float(b.get("vol") or 0) for b in day_m5[-lookback:]]
    return sum(vols) / len(vols) if vols else 0.0


def _near_support(stop_price: float, daily_bars: Optional[List[dict]],
                  chips: Optional[List[dict]], proximity_pct: float) -> bool:
    """止损价是否贴近前期低点（近 20 日最低，不含当日）或筹码成本峰（cost_50pct）。"""
    if stop_price <= 0:
        return False
    refs: List[float] = []
    if daily_bars:
        prior = [b for b in daily_bars
                 if float(b.get("low") or 0) > 0][:-1]  # 不含当日
        lows = [float(b.get("low") or 0) for b in prior[-20:]]
        if lows:
            refs.append(min(lows))
    if chips:
        latest = chips[-1]
        c50 = float(latest.get("cost_50pct") or 0)
        if c50 > 0:
            refs.append(c50)
    if not refs:
        return False
    return any(abs(stop_price - r) / stop_price * 100 <= proximity_pct for r in refs)


def evaluate_stop(current_bar: dict, day_m5: List[dict], stop_price: float,
                  params: Optional[Dict[str, Any]] = None,
                  m1_today: Optional[List[dict]] = None,
                  daily_bars: Optional[List[dict]] = None,
                  chips: Optional[List[dict]] = None) -> Dict[str, Any]:
    """判定一次止损候选触发（调用前提：current_bar.low ≤ stop_price）。

    Args:
        current_bar: 触发 bar（m5，含 low/close/vol/time）
        day_m5: 当日 m5 bars（升序，含触发 bar 及之前）
        stop_price: 当前止损价
        params: t_build._params()（守卫开关/阈值）；None 用内置默认
        m1_today: 当日 1min bars（升序；只使用 ≤ current_bar.time 的部分）
        daily_bars: 标的日线（历史，用于前期低点；不含当日）
        chips: 筹码分布（按 trade_date 升序，最新一条为截至当日）

    Returns:
        {"action": "stop"|"hold", "reason": str, "reset_stop": Optional[float]}
        - stop: 执行止损
        - hold: 跳过（假跌破/未确认）；reset_stop 非空时调用方应把止损基准重置为该值
    """
    params = params or {}
    if not params.get("stop_close_confirm", True):
        return {"action": "stop", "reason": "收盘确认已关闭（原盘中触发口径）", "reset_stop": None}

    low = float(current_bar.get("low") or 0)
    close = float(current_bar.get("close") or 0)
    if low > stop_price:
        return {"action": "hold", "reason": "未触及止损价", "reset_stop": None}
    if stop_price <= 0:
        return {"action": "stop", "reason": "止损价异常", "reset_stop": None}

    proximity = float(params.get("stop_support_proximity_pct", 1.5))
    near = _near_support(stop_price, daily_bars, chips, proximity)

    # 1) 收盘确认：收盘 ≤ 止损 → 真跌破（缩量+贴支撑时仍需更强确认）
    if close <= stop_price:
        shrink = False
        if params.get("stop_volume_filter", True):
            avg = _avg_vol(day_m5)
            v = float(current_bar.get("vol") or 0)
            shrink = avg > 0 and v < avg * 0.7
        if shrink and near:
            return {"action": "hold",
                    "reason": f"收盘破位但缩量且贴支撑位（疑似洗盘）",
                    "reset_stop": close}
        return {"action": "stop", "reason": "收盘确认破位", "reset_stop": None}

    # 2) 收回幅度
    recovery = (close - stop_price) / stop_price * 100
    threshold = float(params.get("stop_recovery_pct", 1.0))
    effective_thr = max(threshold * 0.5, 0.5) if near else threshold
    if recovery >= effective_thr:
        return {"action": "hold",
                "reason": f"假跌破-收回幅度{recovery:.2f}%≥{effective_thr:.2f}%",
                "reset_stop": close}

    # 3) 分钟企稳：触发时刻前连续 N 根 1min 收盘高于止损价
    n = int(params.get("stop_confirm_bars", 5) or 0)
    if n > 0 and m1_today:
        cur_t = str(current_bar.get("time") or "")
        upto = [b for b in m1_today if str(b.get("time") or "") <= cur_t]
        tail = upto[-n:]
        if len(tail) >= n and all(float(b.get("close") or 0) > stop_price for b in tail):
            return {"action": "hold",
                    "reason": f"企稳-连续{n}根1min收盘高于止损价",
                    "reset_stop": close}

    # 4) 缩量过滤（未收回）：缩量插针 → 疑似洗盘，hold 等更强确认
    if params.get("stop_volume_filter", True):
        avg = _avg_vol(day_m5)
        v = float(current_bar.get("vol") or 0)
        if avg > 0 and v < avg * 0.7:
            return {"action": "hold",
                    "reason": f"缩量插针（vol {v:.0f}<均量{avg:.0f}×0.7），疑似洗盘",
                    "reset_stop": close if near else None}

    return {"action": "hold",
            "reason": f"盘中插针-收盘未确认（收回{recovery:.2f}%）",
            "reset_stop": None}
