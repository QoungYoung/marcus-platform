# -*- coding: utf-8 -*-
"""趋势突破 Agent 的薄桥：一次读卡，逐票判定主线/负样本/ETF 上下文。

用法：
    ctx = build_context("20260812")
    r = check_stock("002371.SZ", "北方华创", "半导体/芯片", ctx)
    if r["allowed"]: ...
"""
from __future__ import annotations
from typing import Any, Dict, Optional

try:
    from .daily_card import build_card
    from .negative_filter import evaluate
except ImportError:
    from daily_card import build_card
    from negative_filter import evaluate


def build_context(date8: str) -> Dict[str, Any]:
    card = build_card(date8)
    dirs = card.get("directions") or {}
    core = [x["theme"] for x in (dirs.get("directions") or [])]
    watch = [x["theme"] for x in (dirs.get("watch_directions") or [])]
    etf_by_theme = {}
    for it in card.get("instruments") or []:
        if it.get("theme"):
            etf_by_theme[it["theme"]] = list(it.get("etf") or [])
    return {"date": date8, "card": card, "themes": set(core) | set(watch),
            "core_themes": set(core), "watch_themes": set(watch),
            "etf_by_theme": etf_by_theme}


def _gate(card: Dict[str, Any], sleeve: str, auto_confirm: bool = False) -> Optional[str]:
    reg = card.get("regime") or {}
    if card.get("no_trade"):
        return "no_trade:%s" % (card.get("reason") or "")
    q = (card.get("directions") or {}).get("quality")
    if q not in ("pit_market", "pit_prev_close", "explicit"):
        return "direction_stale:%s" % q
    if sleeve == "core":
        if not reg.get("allow_new_base"):
            return "allow_new_base_false"
        if reg.get("confirm_needed") and not auto_confirm:
            return "confirm_needed"
    elif not reg.get("allow_t"):
        return "allow_t_false"
    return None


def check_stock(ts_code: str, name: str, theme: str, ctx: Dict[str, Any],
                sleeve: str = "core", auto_confirm: bool = False) -> Dict[str, Any]:
    card = ctx["card"]
    gate = _gate(card, sleeve, auto_confirm)
    if gate:
        return {"allowed": False, "reason": gate, "theme": theme, "negative_status": None}
    if theme not in ctx["themes"]:
        return {"allowed": False, "reason": "theme_not_in_mainline", "theme": theme, "negative_status": None}
    neg = evaluate([{"code": ts_code, "name": name, "theme": theme}], sleeve=sleeve, date8=ctx["date"])
    if any(x.get("code") == ts_code for x in neg.get("blocked", [])):
        return {"allowed": False, "reason": "negative_blocked", "theme": theme, "negative_status": "blocked"}
    if any(x.get("code") == ts_code for x in neg.get("tactical_only", [])):
        return {"allowed": False, "reason": "tactical_only", "theme": theme, "negative_status": "tactical_only"}
    status = "ok"
    for key in ("watch", "penalized"):
        if any(x.get("code") == ts_code for x in neg.get(key, [])):
            status = key
            break
    # ETF 只做上下文：保留 codes 兼容旧调用，同时给出 rank/role/score 明细
    items = list((ctx.get("etf_by_theme") or {}).get(theme) or [])
    if not items:
        for it in card.get("instruments") or []:
            if it.get("theme") == theme:
                items = list(it.get("etf") or [])
                break
    etfs = [str(x.get("code")) for x in items if x.get("code")]
    summary = ",".join("%s(%s%s)" % (
        x.get("code"), x.get("score") if x.get("score") is not None else "-",
        ",%s" % x.get("sleeve") if x.get("sleeve") else "") for x in items if x.get("code"))
    return {"allowed": True, "reason": "ok", "theme": theme, "negative_status": status,
            "etf_context": etfs, "etf_candidates": items, "etf_summary": summary,
            "regime": (card.get("regime") or {}).get("regime")}
