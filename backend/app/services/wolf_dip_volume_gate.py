# -*- coding: utf-8 -*-
"""wolf_dip_volume_gate.py — 低吸买腿的**放量破位门**（2026-09-19 用户拍板 A）。

## 语料

· 254 低吸买点的原话口径：「**破前低 + 缩量**」才是低吸（`docs/wolf-dip-entry-rule.md`；我们的
  `custom_prevlow` 表达式本身也带量比 ≤0.9 的缩量条件）；
· 2025-06-25「只要看见**没量就别追**，没意义」（同一维度的反向表述）；
· 2026-01-12「**等收盘确认破位**出清」/ 2026-01-29「收盘跌破我才出」——放量破位属"下跌未止"，不是低吸位。

## 为什么（用户问「jan11 是怎么把第一次回踩也拦下的呢」）

0107 13:45 SH601138 的**回踩买点**（62.627/62.690）：jan11 的 AI 判 `await_retry`（理由：「13:45 一根
创日内新低 62.620 **并放量 6.85M**（本段最大量），属**下跌未止的追跌位**」），jan12 的 AI 判 `executed`
（理由：「6.85M **未超前根** 7.75M，不构成量比骤升的恐慌追跌」）——**同一份量能数据、两臂读成相反**，
因为这一侧当时没有任何确定性判据。本门把它固化：**放量 + 破前低 ⇒ 买腿不执行**（反之，缩量回踩照旧放行）。

## 判据（买腿专用；数据缺失一律 fail-open）

  当日最低 ≤ 前低 × (1 + 容差)   （破前低）
  且 量比 ≥ `WOLF_DIP_VOL_EXPAND`（默认 1.5，放量）
  ⇒ 拦：不是低吸而是杀跌（254 要缩量）。

## 数据来源（执行口从触发快照取，两种形态都归一）

· 形态腿（`wolf_zheng_t_buy` 等）：`snapshot.prev_low / today_low / vol_ratio`（2026-09-19 起由
  `t_monitor._insert_wolf_trigger` 写入）；
· 条件腿（`custom_prevlow` 等）：`snapshot.fields.vol_ratio`、`fields.quote.low`、`trigger_price`（= 前低锚）。

开关 `WOLF_DIP_VOL_GATE`：**库内默认 0**（生产逐位不变），回测由 pins/驱动置 1。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

ON = str(os.getenv("WOLF_DIP_VOL_GATE", "0")).strip().lower() in ("1", "true", "yes", "on")
EXPAND = float(os.getenv("WOLF_DIP_VOL_EXPAND", "1.5"))     # 放量阈值（量比）
PREVLOW_TOL = float(os.getenv("WOLF_DIP_PREVLOW_TOL", "0.005"))

_STATS: Dict[str, Any] = {"checked": 0, "blocked": 0, "no_data": 0, "errors": 0}


def enabled() -> bool:
    return ON


def stats() -> Dict[str, Any]:
    return {"on": ON, "expand": EXPAND, "prevlow_tol": PREVLOW_TOL, **_STATS}


def verdict(prev_low: Optional[float], today_low: Optional[float], vol_ratio: Optional[float],
            price: Optional[float] = None, side: str = "buy") -> Tuple[bool, str]:
    """返回 (ok, why)。ok=False ⇒ 该买腿不执行（放量破前低 = 杀跌，不是 254 低吸位）。"""
    if not ON:
        return True, "WOLF_DIP_VOL_GATE=0（放量破位门关闭）"
    if str(side or "buy").lower() not in ("buy", "买入"):
        return True, "卖腿不适用"
    _STATS["checked"] += 1
    try:
        _pl = float(prev_low or 0)
        _tl = float(today_low or 0)
        _vr = float(vol_ratio) if vol_ratio is not None else None
    except Exception:
        _STATS["errors"] += 1
        return True, "数据不可解析→放行"
    if _pl <= 0 or _tl <= 0 or _vr is None:
        _STATS["no_data"] += 1
        return True, "缺前低/当日低/量比→放行"
    broke = _tl <= _pl * (1.0 + PREVLOW_TOL)
    if not broke:
        return True, f"未破前低（当日低 {_tl:.3f} > 前低 {_pl:.3f}）→放行"
    if _vr < EXPAND:
        return True, f"破前低但缩量（量比 {_vr:.2f} < {EXPAND:.2f}）→符合 254 低吸口径，放行"
    _STATS["blocked"] += 1
    return False, (f"放量破前低：当日低 {_tl:.3f} ≤ 前低 {_pl:.3f} 且量比 {_vr:.2f} ≥ {EXPAND:.2f} —— "
                   "254 低吸要求「破前低+**缩量**」，放量破位属下跌未止不是低吸位"
                   "（他 2025-06-25「只要看见没量就别追」、2026-01-12「等收盘确认破位」）")