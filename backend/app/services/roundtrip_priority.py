# -*- coding: utf-8 -*-
"""roundtrip_priority.py — A1（2026-09-14）：「+3% 目标优先」+「破黄线需确认」。

背景（真 m5 验收结论，docs/exit-rules-m5-report.md）：
  · 用标的自己的分时均价线（黄线）判"破线"，**无论全天还是只在他两个做 T 窗口内，33/33 条腿都会破**
    → 破线是持仓期间几乎必然发生的状态，**不是择时信号**；
  · 生产 t_monitor._check_roundtrip_sell 原先把 vwap_break 当**优先离场**条件 →
    "低吸后反弹 +3% 卖回"大多被提前触发（V1 +1.29% → V3c −0.06%）。

狼大原话（两条，正好对应"目标"与"破线"）:
  · 2026-08-04 10:48「在这个半小时内有个绝对不能破的点 就是日均线那条黄线，**一旦突发跌破直接走**。
    **如果没跌破就找这半小时的高点**。」
  · 2026-09-02「2 点到了 力度不够…今天目标 3%-4% 实际 2% 结束今天半导体做 T 操作」；
    2026-08-13 楼275/280「至少能有吃 **3-5 个点**的幅度吧」。

A1 口径:
  1. **目标优先**：现价 ≥ 低吸均价×(1+兑现幅度) → 直接卖（对应"没跌破就找这半小时的高点"）；
  2. **破线要确认**（对应"**突发**跌破"）：满足其一即算确认 ——
     a. **幅度**：现价 ≤ 黄线×(1 − WOLF_VWAP_BREAK_PCT，默认 0.5%)（"不是贴着线蹭一下"）；
     b. **持续**：连续 WOLF_VWAP_BREAK_ROUNDS（默认 2 轮，TMonitor 一轮 30s）现价都在黄线下方；
  3. 回退：`WOLF_RT_PRIORITY=vwap_first` 回旧行为（破线优先）；把 PCT=0 且 ROUNDS=1 即"一破就走"。

纯函数（可单测）：roundtrip_decision(cur, buy_avg, avg, up, streak, ...) → (action, why, new_streak)
  action ∈ {"sell_target", "sell_vwap", "wait"}。
"""
from __future__ import annotations

import os
from typing import Optional, Tuple


def _f(name: str, dflt: float) -> float:
    try:
        return float(os.getenv(name, str(dflt)))
    except (TypeError, ValueError):
        return dflt


def _i(name: str, dflt: int) -> int:
    try:
        return int(float(os.getenv(name, str(dflt))))
    except (TypeError, ValueError):
        return dflt


def priority() -> str:
    """target_first（默认，A1）｜ vwap_first（旧行为，回退用）。"""
    return (os.getenv("WOLF_RT_PRIORITY", "target_first") or "target_first").strip().lower()


def vwap_break_pct() -> float:
    """"突发跌破"的幅度确认阈值（默认 0.5%）。0 = 不做幅度确认。"""
    try:
        return max(float(os.getenv("WOLF_VWAP_BREAK_PCT", "0.005")), 0.0)
    except (TypeError, ValueError):
        return 0.005


def vwap_break_rounds() -> int:
    """"持续跌破"的轮数确认（默认 2 轮；1 = 一破就走）。"""
    try:
        return max(int(float(os.getenv("WOLF_VWAP_BREAK_ROUNDS", "2"))), 1)
    except (TypeError, ValueError):
        return 2


def roundtrip_decision(cur: float, buy_avg: float, avg: Optional[float], up: float,
                       streak: int = 0) -> Tuple[str, str, int]:
    """等量换手腿的离场判定（纯函数）。

    返回 (action, why, new_streak)；action ∈ {sell_target, sell_vwap, wait}。
    · cur/buy_avg：现价 / 当日低吸均价；avg：分时均价线（黄线，quote.average）；up：兑现幅度（如 0.03）。
    · streak：**上一轮**连续在黄线下的次数（调用方持久化）。
    """
    if cur <= 0 or buy_avg <= 0:
        return ("wait", "无有效价格", streak)
    target = buy_avg * (1.0 + float(up))
    below = bool(avg and avg > 0 and cur < float(avg))
    new_streak = (streak + 1) if below else 0

    hit_target = cur >= target
    pct = vwap_break_pct()
    deep = bool(below and pct > 0 and avg and cur <= float(avg) * (1.0 - pct))
    sustained = bool(below and new_streak >= vwap_break_rounds())
    confirmed = bool(deep or sustained)
    why_c = ("突发跌破%.2f%%" % ((1 - cur / float(avg)) * 100)) if deep else \
            ("连续%d轮在黄线下" % new_streak) if sustained else ""

    if priority() == "vwap_first":          # 旧行为（回退开关）
        if confirmed:
            return ("sell_vwap", "黄线破位(%s) [vwap_first]" % why_c, new_streak)
        if hit_target:
            return ("sell_target", "达到兑现幅度 +%.1f%%" % (float(up) * 100), new_streak)
        return ("wait", "未达标且未破线", new_streak)

    # A1：**目标优先**（"没跌破就找这半小时的高点"）
    if hit_target:
        return ("sell_target", "达到兑现幅度 +%.1f%%（狼大 2026-08-13「3-5个点」）" % (float(up) * 100), new_streak)
    if confirmed:
        return ("sell_vwap", "黄线破位确认(%s)（狼大 2026-08-04「突发跌破直接走」）" % why_c, new_streak)
    return ("wait", ("在黄线下第%d轮（未确认）" % new_streak) if below else "未达标且未破线", new_streak)
