# -*- coding: utf-8 -*-
"""wolf_fib_target.py — G2：个股止盈点 = **前一波拉升幅度的 0.618 位**（2026-09-14 落地）。

狼大原话（XLS 2026 段，逐字）:
  · 2026-04-23「我说一下 我个人认为 如果手上的泛科技票 如果**超过或者到了 个股的前一波拉升幅度的
    0.618 位 就是我的止盈点了** 这个个股的止盈位置」（docs/wolf-daily-log-xls2025.md / -xls2026.md）
  · 2026-03-05「黄金分割**只用 0.382 和 0.618**」← 参数由他自己给
  · 同族：「做反抽 我的目标就是 5 个点 目前药已经达到了 我就撤了 卖飞总比亏损好」（2025-04-01）

口径（可算化）：
  · **前一波拉升** = 买入日之前**最近一个已确认的"低 → 高"完整波段**（摆动点用两侧各 k 根确认，
    默认 k=3；只看近 lookback 根，默认 120）；
  · **止盈位** = 该波段的低点 + ratio×(高点 − 低点)，ratio 默认 **0.618**（WOLF_FIB_RATIO 可调）；
  · **触发** = 现价 ≥ 止盈位 ∧ **有浮盈**（止盈语义；亏损侧交给止损，避免双杀）；
  · 卖出量 = **T 仓**（sellable − 底仓 floor，保留底仓）——他这句是"止盈点"（到点走），
    但底仓不动的原则（2025-05-27）同样适用；数量由 t_monitor 的卖出管道推导。

纯函数模块（可单测）：摆动点/前一波/止盈位/触发判定；数据由调用方喂 bars。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple


def enabled() -> bool:
    return os.getenv("WOLF_FIB_TARGET", "1").strip() not in ("0", "false", "no")


def ratio_default() -> float:
    try:
        return float(os.getenv("WOLF_FIB_RATIO", "0.618"))
    except (TypeError, ValueError):
        return 0.618


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def swings(bars: List[dict], i: int, k: int = 3) -> List[Tuple[int, str, float]]:
    """已确认的摆动点（两侧各 k 根）：[(idx, 'low'|'high', price)]，按时间升序。

    只用 bars[0..i] 的信息（PIT）：判定 j 为摆动点需要 j+k ≤ i。
    """
    out = []
    for j in range(k, i - k + 1):
        hs = [_f(bars[m].get("high")) for m in range(j - k, j + k + 1)]
        ls = [_f(bars[m].get("low")) for m in range(j - k, j + k + 1)]
        if not hs or not ls:
            continue
        hj, lj = _f(bars[j].get("high")), _f(bars[j].get("low"))
        if hj >= max(hs) and hj > 0:
            out.append((j, "high", hj))
        elif lj <= min(ls) and lj > 0:
            out.append((j, "low", lj))
    # 同一根既可能是高点也可能是低点：按价格极端性取一（优先与邻域差异更大的）
    dedup = {}
    for idx, kind, px in out:
        dedup.setdefault(idx, (idx, kind, px))
    return [dedup[j] for j in sorted(dedup)]


def prev_up_leg(bars: List[dict], i: int, k: int = 3, lookback: int = 120
                ) -> Optional[Tuple[int, float, int, float]]:
    """买入日 i 之前**最近一个已确认的"低 → 高"上涨波**：返回 (low_idx, low_px, high_idx, high_px)。"""
    lo_from = max(0, i - lookback)
    sw = [s for s in swings(bars, i, k) if s[0] >= lo_from]
    if not sw:
        return None
    # 取最后一个已确认高点；再取它之前最近的已确认低点
    highs = [s for s in sw if s[1] == "high"]
    if not highs:
        return None
    hidx, _, hpx = highs[-1]
    lows = [s for s in sw if s[1] == "low" and s[0] < hidx]
    if not lows:
        return None
    lidx, _, lpx = lows[-1]
    if hpx <= lpx:
        return None
    return (lidx, lpx, hidx, hpx)


def fib_target(bars: List[dict], i: int, ratio: Optional[float] = None,
               k: int = 3, lookback: int = 120) -> Optional[Dict[str, Any]]:
    """止盈位（他 2026-04-23 的"0.618 位"）：低点 + ratio×(高点 − 低点)。"""
    leg = prev_up_leg(bars, i, k, lookback)
    if not leg:
        return None
    lidx, lpx, hidx, hpx = leg
    r = ratio_default() if ratio is None else float(ratio)
    return {"target": round(lpx + r * (hpx - lpx), 4), "ratio": r,
            "leg_low": round(lpx, 4), "leg_high": round(hpx, 4),
            "leg_low_idx": lidx, "leg_high_idx": hidx,
            "span_pct": round((hpx / lpx - 1.0) * 100.0, 2) if lpx else None}


def fib_decision(price: float, target: Optional[float], cost: float = 0.0,
                 done: bool = False) -> Tuple[str, str]:
    """(action, reason)：达到/超过止盈位且**有浮盈** → `sell`；否则 `wait`。"""
    if done:
        return ("wait", "今日已止盈过（去抖）")
    if not target or target <= 0:
        return ("wait", "没有可用的前一波波段（不足以算 0.618 位）")
    if price <= 0:
        return ("wait", "无有效现价")
    if cost > 0 and price <= cost:
        return ("wait", "无浮盈（止盈语义；亏损侧交给止损）")
    if price >= target:
        return ("sell", "现价 %.3f ≥ 前一波拉升 0.618 位 %.3f（狼大 2026-04-23「到了…就是我的止盈点了」）"
                % (price, target))
    return ("wait", "现价 %.3f < 止盈位 %.3f" % (price, target))
