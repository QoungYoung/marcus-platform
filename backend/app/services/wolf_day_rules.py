# -*- coding: utf-8 -*-
"""wolf_day_rules.py — G7（大涨日多卖/大跌日多买）+ G8（出上影线停机）（2026-09-14 落地）。

狼大原话:
  · **G7** 2025-01-23「**大涨之日少买票，多卖票，大跌之日多买票 少卖票**。因为指数方面没什么太大隐患…」
  · **G8** 2025-07-17「任何时候 看见机器人板块**出上影线 立马停止做T**，保持 30% 机器人底仓就别动了」
  · 同族 2025-05-06「**放量上影线，2 倍 10 日均量以上** 不出这个就很难见顶」（已落在 G1 的 S2）
  · 同族「**缩量不参与**」「量能由缩转放是介入信号」（买侧的语气，见 playbook §7）

口径（可算化，含"代理"标注）:
  · **大涨/大跌日**：上证当日涨跌幅（现价 vs 昨收）≥ WOLF_UP_DAY_PCT（默认 +1.0%）→ `up`；
    ≤ −WOLF_UP_DAY_PCT → `down`；其余 `neutral`。
    → **up 日**：兑现类卖腿的浮盈门槛下调（WOLF_UP_DAY_TP_FACTOR，默认 0.67 → +3% 变 +2%）= "多卖票"；
    → **down 日**：**当日不新增兑现类卖腿**（少卖票；止损/破位/被动止盈等保护动作照旧）；
    → 买侧我们本来就"只在下跌里买"（狼大原话），故不额外放宽——**不新造"大跌日放宽买入"的机制**。
  · **上影线停机**：最近一根**已完成**日线的上影 ≥ WOLF_SHADOW_RATIO（默认 0.3）×全幅 → 该标的当日
    **停止做 T**（不新开做T买腿、不做兑现类卖腿）；保护性卖出照旧。取不到日线 → 不停机（不猜）。

纯判定函数（可单测）；状态与调用点在 t_monitor。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple


def enabled() -> bool:
    return os.getenv("WOLF_DAY_RULES", "1").strip() not in ("0", "false", "no")


def up_pct() -> float:
    try:
        return abs(float(os.getenv("WOLF_UP_DAY_PCT", "1.0")))
    except (TypeError, ValueError):
        return 1.0


def up_tp_factor() -> float:
    """大涨日兑现门槛系数（默认 0.67 → 3% 变 2%）。"""
    try:
        return float(os.getenv("WOLF_UP_DAY_TP_FACTOR", "0.67"))
    except (TypeError, ValueError):
        return 0.67


def shadow_ratio() -> float:
    try:
        return float(os.getenv("WOLF_SHADOW_RATIO", "0.3"))
    except (TypeError, ValueError):
        return 0.3


def day_bias(idx_pct: Optional[float]) -> str:
    """指数当日涨跌幅 → 'up' / 'down' / 'neutral'（狼大 2025-01-23）。"""
    if idx_pct is None:
        return "neutral"
    th = up_pct()
    if idx_pct >= th:
        return "up"
    if idx_pct <= -th:
        return "down"
    return "neutral"


def tp_threshold(base_pct: float, bias: str) -> float:
    """兑现门槛按日型调整：up 日下调（多卖票）；down/neutral 保持。"""
    if bias == "up":
        return round(float(base_pct) * up_tp_factor(), 4)
    return float(base_pct)


def allow_realize_sell(bias: str) -> bool:
    """down 日：**不新增兑现类卖腿**（少卖票）；保护性卖出（止损/破位/被动止盈）不走这个门。"""
    return bias != "down"


def has_upper_shadow(bar: Optional[Dict[str, Any]], ratio: Optional[float] = None) -> bool:
    """该日线是否"上影线"（上影 ≥ ratio×全幅）。他 2025-07-17「出上影线 立马停止做T」。"""
    if not bar:
        return False
    try:
        o, h, l, c = (float(bar.get("open") or 0), float(bar.get("high") or 0),
                      float(bar.get("low") or 0), float(bar.get("close") or 0))
    except (TypeError, ValueError):
        return False
    if h <= l:
        return False
    r = shadow_ratio() if ratio is None else float(ratio)
    return (h - max(o, c)) >= r * (h - l)


def shadow_stop(bars: List[dict], lookback: int = 1) -> Tuple[bool, str]:
    """最近 lookback 根**已完成**日线是否触发"停机"。返回 (stop, reason)。"""
    if not bars:
        return (False, "no_bars")
    for b in bars[-max(1, lookback):]:
        if has_upper_shadow(b):
            return (True, "上影线≥%.0f%%全幅（狼大 2025-07-17「看见出上影线 立马停止做T」）"
                    % (shadow_ratio() * 100))
    return (False, "")
