# -*- coding: utf-8 -*-
"""wolf_stop_layer.py — 统一止损/破位层（v3，门面）。

只做编排，不 import 旧规则模块：
  · point_engine     → 计算点位（不硬编码）
  · key level        → 当天 1-3 个关键位
  · down-wave        → G3 15% cap（2026-01-17 / 2026-08-27；2026-10-25 拍板 15）
  · computed break   → 目标 = tier_target_pct(operation)（2026-01-17 分档）
  · individual       → alert only（用户 2026-10-23 拍板）
  · guard            → 收盘确认 / 盘中不算 / 缩量不算 / 时点门

动作由调用方（t_monitor）执行；本模块只返回 `StopDecision`。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.services import point_engine as PE

ACTION_TYPES = frozenset({
    "breakdown_reduce", "reclaim_failure", "rebound_exit",
    "target_reduce", "hold", "alert", "observe", "restore",
})

DEFAULT_BUFFER = 0.003
DEFAULT_VOL_DIRECT = 1.2
DEFAULT_VOL_SHRINK = 0.8


@dataclass
class StopContext:
    symbol: str = "000001.SH"
    asof: str = ""
    phase: str = "close"          # intraday|close|replay
    bars: List[Dict[str, Any]] = field(default_factory=list)   # 含当日
    wave: Dict[str, Any] = field(default_factory=dict)
    operation: str = "side"
    position: Dict[str, Any] = field(default_factory=dict)
    stock_bars: Optional[List[Dict[str, Any]]] = None
    stock_symbol: str = ""
    sector_bars: Optional[List[Dict[str, Any]]] = None
    sector_symbol: str = ""
    anchors: List[Dict[str, Any]] = field(default_factory=list)
    time_ok: bool = True
    params: Dict[str, Any] = field(default_factory=dict)
    target_pct: Optional[float] = None     # 可显式给；None 用 tier_target_pct


@dataclass
class StopDecision:
    action: str                  # hold|alert|reduce_to|restore|no_new
    action_type: str = "hold"    # breakdown_reduce|reclaim_failure|rebound_exit|target_reduce|...
    layer: str = "none"          # index|trend|stock|guard|none
    target_pct: Optional[float] = None
    no_new: bool = False
    severity: str = "B0"
    reason: str = ""
    evidence: List[str] = field(default_factory=list)
    level: Optional[Dict[str, Any]] = None
    level_state: str = ""        # holding|broken|resistance
    status_text: str = ""        # 已跌破/支撑中/上方压力
    next_support: Optional[Dict[str, Any]] = None
    source: str = "corpus"       # corpus|proxy|engineering
    invalidation: str = ""


def _f(x: Any, d: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def format_level_status(level: Optional[Dict[str, Any]], close: float) -> str:
    """把“已破位/支撑中/上方压力”说清楚，不要拿下一道支撑冒充触发线。"""
    if not level:
        return ""
    price = _f(level.get("price"))
    state = str(level.get("state") or "").strip().lower()
    if price <= 0:
        return ""
    if price > close:
        if state == "broken":
            return "已跌破 %.2f（现价 %.2f 在其下方）" % (price, close)
        if state == "resistance":
            return "上方压力 %.2f" % price
        return "上方 %.2f（未收复）" % price
    if state == "broken":
        return "已跌破 %.2f（现价 %.2f 在其上方）" % (price, close)
    return "下方支撑 %.2f" % price


def _vol_ratio(bars: List[Dict[str, Any]], i: int, win: int = 20) -> Optional[float]:
    if i <= 0 or i >= len(bars):
        return None
    cur = _f(bars[i].get("vol") or bars[i].get("amount"))
    if cur <= 0:
        return None
    prev = [_f(b.get("vol") or b.get("amount")) for b in bars[max(0, i - win):i]]
    prev = [v for v in prev if v > 0]
    if len(prev) < 5:
        return None
    return cur / (sum(prev) / len(prev))


def _tier_target(operation: str) -> Optional[float]:
    try:
        from app.services.wolf_discipline import tier_target_pct
        v = tier_target_pct(operation)
        return (float(v) / 100.0) if v is not None else None
    except Exception:
        return None


def _cfg(ctx: StopContext) -> Dict[str, Any]:
    p = dict(ctx.params or {})
    p.setdefault("buffer", DEFAULT_BUFFER)
    p.setdefault("vol_direct", DEFAULT_VOL_DIRECT)
    p.setdefault("vol_shrink", DEFAULT_VOL_SHRINK)
    return p


def _key_break(prev_bars: List[Dict[str, Any]], today: Dict[str, Any],
               ctx: StopContext) -> tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    if len(prev_bars) < 20:
        return None, []
    pe = PE.compute_points(prev_bars, asof=prev_bars[-1].get("trade_date") or prev_bars[-1].get("date"),
                           domain="index", symbol=ctx.symbol, anchors=ctx.anchors)
    keys = PE.key_levels(pe, n=3)
    buf = _f(_cfg(ctx)["buffer"], DEFAULT_BUFFER)
    close = _f(today.get("close"))
    broken = [k for k in keys if close < _f(k.get("price")) * (1.0 - buf)]
    return (broken[0] if broken else None), keys


def evaluate(ctx: StopContext,
             target_fn: Optional[Callable[[str], Optional[float]]] = None) -> StopDecision:
    """返回统一决策；不读库、不下单。"""
    bars = [b for b in (ctx.bars or []) if _f(b.get("close")) > 0]
    if len(bars) < 21:
        return StopDecision("hold", action_type="hold", layer="none", reason="bars<21，数据不足", source="engineering")
    today, prev = bars[-1], bars[:-1]
    op = (ctx.operation or ctx.wave.get("operation") or "side").strip().lower()
    level = str(ctx.wave.get("level") or "").strip().lower()
    down_wave = (level == "down") or (op == "exit")

    # 1) down-wave：G3 15% cap（2026-10-25 拍板；不再写死 30）
    if down_wave:
        _tgt = float(os.getenv("WOLF_DOWN_WAVE_TARGET", "0.15"))
        if _tgt <= 0:
            _tgt = 0.15
        return StopDecision("reduce_to", action_type="breakdown_reduce", layer="index",
                            target_pct=_tgt, no_new=True, severity="B3", source="corpus",
                            reason="wave.level=down / operation=exit → G3 %.0f%% cap（2026-01-17 / 2026-10-25 拍板 15%%）" % (_tgt * 100),
                            evidence=["level=%s operation=%s target=%.0f%%" % (level, op, _tgt * 100)],
                            invalidation="收盘站回关键位/趋势线")

    # 2) 盘中/时点门：只 alert，不动作
    if str(ctx.phase) == "intraday":
        return StopDecision("observe", action_type="observe", layer="guard", source="corpus",
                            reason="盘中不算（2025-11-21）；只预览，不写动作")
    if not ctx.time_ok:
        return StopDecision("alert", action_type="alert", layer="guard", source="corpus",
                            reason="13:00-14:30 时点门：只预警，不执行（2026-03-23）")

    # 3) 指数计算级关键位破位 → operation 分档目标
    key, keys = _key_break(prev, today, ctx)
    if key:
        vr = _vol_ratio(bars, len(bars) - 1)
        shrink = vr is not None and vr < _f(_cfg(ctx)["vol_shrink"], DEFAULT_VOL_SHRINK)
        if shrink:
            return StopDecision("alert", action_type="alert", layer="index", level=key,
                                level_state=str(key.get("state") or ""),
                                status_text=format_level_status(key, _f(today.get("close"))),
                                source="proxy", severity="B1",
                                reason="收盘破关键位但缩量 → 等反抽不过再确认（2026-03-04）",
                                evidence=["key=%s %.2f vol_ratio=%.2f" % (
                                    key.get("type"), _f(key.get("price")), vr or 0)],
                                invalidation="收盘站回 %.2f" % _f(key.get("price")))
        tgt = ctx.target_pct
        if tgt is None:
            tgt = (target_fn(op) if target_fn else None)
        if tgt is None:
            tgt = _tier_target(op)
        if tgt is None:
            return StopDecision("alert", action_type="alert", layer="index", level=key,
                                level_state=str(key.get("state") or ""),
                                status_text=format_level_status(key, _f(today.get("close"))),
                                source="proxy", severity="B1",
                                reason="收盘破关键位，但 operation 无分档目标 → 只提示",
                                evidence=["key=%s %.2f" % (key.get("type"), _f(key.get("price")))],
                                invalidation="收盘站回 %.2f" % _f(key.get("price")))
        return StopDecision("reduce_to", action_type="breakdown_reduce", layer="index",
                            target_pct=float(tgt), level=key,
                            level_state=str(key.get("state") or ""),
                            status_text=format_level_status(key, _f(today.get("close"))),
                            source="proxy", severity="B2",
                            reason="收盘破关键位 %s %.2f + 量比 %s → 目标 %.0f%%（operation=%s）" % (
                                key.get("type"), _f(key.get("price")),
                                ("%.2f" % vr) if vr is not None else "?",
                                float(tgt) * 100, op),
                            evidence=["key=%s" % key.get("type"),
                                      "confidence=%.2f" % _f(key.get("confidence")),
                                      "confluence=%s" % key.get("confluence")],
                            invalidation="收盘站回 %.2f" % _f(key.get("price")))

    # 4) 站回关键位 → 恢复到 operation 目标
    if ctx.position.get("was_broken"):
        tgt = ctx.target_pct if ctx.target_pct is not None else (
            target_fn(op) if target_fn else _tier_target(op))
        if tgt is not None:
            return StopDecision("restore", action_type="reclaim", layer="index", target_pct=float(tgt), source="proxy",
                                reason="已站回关键位 → 恢复 operation 目标 %.0f%%" % (float(tgt) * 100),
                                invalidation="再次收盘跌破关键位")

    # 5) 个股/板块：alert only
    for bars_s, sym, dom in ((ctx.stock_bars, ctx.stock_symbol, "stock"),
                             (ctx.sector_bars, ctx.sector_symbol, "sector")):
        if not bars_s or len(bars_s) < 21:
            continue
        sb = [b for b in bars_s if _f(b.get("close")) > 0]
        if len(sb) < 21:
            continue
        k, _ = _key_break(sb[:-1], sb[-1], StopContext(symbol=sym or ctx.symbol,
                                                       bars=sb, params=ctx.params))
        if k:
            return StopDecision("alert", action_type="alert", layer=dom, level=k,
                                level_state=str(k.get("state") or ""),
                                status_text=format_level_status(k, _f(sb[-1].get("close"))),
                                source="corpus", severity="B2",
                                reason="%s 收盘破关键位 %s %.2f → 只提示（个股不上自动止损）" % (
                                    dom, k.get("type"), _f(k.get("price"))),
                                evidence=["key=%s" % k.get("type")],
                                invalidation="收盘站回 %.2f" % _f(k.get("price")))

    return StopDecision("hold", action_type="hold", layer="none", source="corpus", reason="无破位信号")


def guard_stop(current_bar: Dict[str, Any], day_m5: Optional[List[Dict[str, Any]]],
               stop_price: float, params: Optional[Dict[str, Any]] = None,
               m1_today: Optional[List[Dict[str, Any]]] = None,
               daily_bars: Optional[List[Dict[str, Any]]] = None,
               chips: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """收盘确认/假跌破守卫（兼容旧 evaluate_stop 调用签名，2026-10-23 迁入）。

    只做三件事：收盘破位、收回幅度、缩量贴支撑；数据不足时按原口径 stop（fail-open）。
    """
    p = dict(params or {})
    if not p.get("stop_close_confirm", True):
        return {"action": "stop", "reason": "收盘确认关闭（原盘中口径）", "reset_stop": None}
    sp = float(stop_price or 0)
    close = float((current_bar or {}).get("close") or 0)
    low = float((current_bar or {}).get("low") or close)
    if sp <= 0:
        return {"action": "stop", "reason": "止损价异常", "reset_stop": None}
    if low > sp:
        return {"action": "hold", "reason": "未触及止损价", "reset_stop": None}
    if close > sp:
        return {"action": "hold", "reason": "收盘未破止损价", "reset_stop": None}
    recovery = (close - sp) / sp * 100.0
    thr = float(p.get("stop_recovery_pct", 1.0))
    if recovery >= thr:
        return {"action": "hold", "reason": "假跌破-收回幅度%.2f%%≥%.2f%%" % (recovery, thr),
                "reset_stop": close}
    # 缩量 + 贴支撑：疑似洗盘，不卖
    try:
        v_now = float((current_bar or {}).get("vol") or 0)
        ref = [float(b.get("vol") or 0) for b in (day_m5 or [])[-20:] if float(b.get("vol") or 0) > 0]
        if v_now > 0 and ref and v_now < (sum(ref) / len(ref)) * 0.8:
            return {"action": "hold", "reason": "收盘破位但缩量（疑似洗盘）", "reset_stop": close}
    except Exception:
        pass
    return {"action": "stop", "reason": "收盘确认破位", "reset_stop": None}
