# -*- coding: utf-8 -*-
"""狼大语料：**尾段规则** + **加仓分步 75/90/100＋利润垫** + **止损按指数破位改写** 的判据层（纯函数）。

语料依据：
· 尾段（2026-09-04）「就等吃最后一段 不管吃不吃得到**我都不会加仓 顶多做T** 然后最后一段撤掉」
  ＋ 蓝图 503「破位**之后的反抽** → **只做 T、不加仓**」（2026-08-21 14:43）
· 加仓（蓝图 201）「确认后（站稳趋势线/回踩到位）＋**有利润垫**；**尾段不加**」；
  操作＝「**分步打满（75%→90%→100%）**或挂线买入」；条件不达就不做
· 止损（蓝图 205/457）「止损**只有指数破位**这一个条件」；「个股止损是'技术策略扛不住波动'的结果」；
  2026-01-29「**收盘跌破我才出**」；2026-03-05「无利空 13 日内下跌 新低后 -3% 是逻辑问题，要控制损失就必须止损」
  2026-08-21「我的策略就没有这个量能割过肉」（**地量不割**）
⚠️ 三条都是策略判据 ⇒ 开关**默认关**，启用前同窗口 A/B。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

STOP_BY_BREAK = str(os.getenv("WOLF_STOP_BY_INDEX_BREAK", "0")).strip().lower() in ("1", "true", "yes", "on")
TAIL_PHASE_ON = str(os.getenv("WOLF_TAIL_PHASE", "0")).strip().lower() in ("1", "true", "yes", "on")
ADD_STAGED_ON = str(os.getenv("WOLF_ADD_STAGED", "0")).strip().lower() in ("1", "true", "yes", "on")
ADD_STEPS = tuple(float(x) for x in os.getenv("WOLF_ADD_STEPS", "75,90,100").split(","))
_STATS: Dict[str, int] = {"stop_blocked": 0, "tail_block": 0, "add_no_cushion": 0, "add_staged": 0}


def stats() -> Dict[str, Any]:
    return {"stop_by_break": bool(STOP_BY_BREAK), "tail_phase": bool(TAIL_PHASE_ON),
            "add_staged": bool(ADD_STAGED_ON), "steps": list(ADD_STEPS), **dict(_STATS)}


def stats_reset() -> None:
    for _k in _STATS:
        _STATS[_k] = 0


# ── 尾段规则 ────────────────────────────────────────────────────────────────
def tail_phase(index_broken: bool, index_up_today: bool, enabled: Optional[bool] = None) -> bool:
    """是否处于"尾段/破位后反抽"状态：**指数已破位 且 当日反抽**（蓝图 503）。

    该状态下：**不加仓（含建仓）、只做 T**。数据缺失 → False（fail-open，不因取数判尾段）。
    """
    on = TAIL_PHASE_ON if enabled is None else bool(enabled)
    if not on:
        return False
    return bool(index_broken) and bool(index_up_today)


def tail_allows(kind: str, has_position: bool, in_tail: bool) -> bool:
    """尾段里什么能做：只允许**做T**（有底仓的低吸/高抛），不允许建仓与加仓。"""
    if not in_tail:
        return True
    k = str(kind or "")
    if not has_position:
        return False            # 建仓：尾段不做
    if k in ("low_buy", "high_sell", "high_sell_then_buy_back", "custom_vwap_sell",
             "custom_support_sell", "wolf_zheng_t_buy", "wolf_dao_t_sell"):
        return True             # 做T 类允许
    return False                # 其余（加仓/253-254 建仓腿等）尾段不做


# ── 加仓分步 75/90/100 ＋ 利润垫 ───────────────────────────────────────────
def next_add_target(current_pos_pct: float, steps=None) -> Optional[float]:
    """当前仓位% → **下一档目标%**（75→90→100）；已到最高档返回 None。"""
    sp = tuple(steps or ADD_STEPS)
    for s in sp:
        if float(current_pos_pct or 0) < s - 1e-9:
            return s
    return None


def add_allowed(pnl_pct: Optional[float], in_tail: bool = False,
                enabled: Optional[bool] = None, min_cushion: float = 0.0) -> bool:
    """加仓前置：**有利润垫**（浮盈 > min_cushion）且**非尾段**；数据缺失 → 不放行（保守）。"""
    on = ADD_STAGED_ON if enabled is None else bool(enabled)
    if not on:
        return True
    if in_tail:
        return False
    try:
        return float(pnl_pct) > float(min_cushion)
    except (TypeError, ValueError):
        return False


# ── 止损按指数破位改写 ─────────────────────────────────────────────────────
def stop_allowed(index_broken: bool, volume_dry: bool = False,
                 enabled: Optional[bool] = None) -> Dict[str, Any]:
    """个股止损是否允许执行：**只有指数破位才允许**（蓝图 205/457）；**地量不割**（08-21）。

    返回 {ok, why}。开关关 → 永远 ok=True（旧行为：按成本/结构线止损）。
    """
    on = STOP_BY_BREAK if enabled is None else bool(enabled)
    if not on:
        return {"ok": True, "why": "未启用（旧口径：按个股止损线执行）"}
    if volume_dry:
        return {"ok": False, "why": "地量不割肉（2026-08-21）"}
    if not index_broken:
        return {"ok": False, "why": "指数未破位 ⇒ 不个股止损（2026-08-25 口径：止损只有指数破位这一个条件）"}
    return {"ok": True, "why": "指数破位 ⇒ 允许持仓止损/减仓评估"}
