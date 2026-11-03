# -*- coding: utf-8 -*-
"""离场规则确定性引擎（规格见 docs/exit-rule-spec.md）。

只算事实/规则信号/建议量，不调用 agent、不下单。
"""
from __future__ import annotations
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

PATTERNS = {"accel_end_2nd_push_failed", "accel_end_after_break",
            "vwap_break", "board_open_no_refill", "none"}
BOARD_PCT = 0.10


def _f(v, d=0.0):
    try: return float(v)
    except Exception: return d


def _norm_time(v: Any) -> str:
    s = re.sub(r"\D", "", str(v or ""))
    if len(s) == 3:
        return "0" + s
    if len(s) in (5, 6):
        return s[:4]
    return s[:12] if len(s) >= 12 else s


def _hm(t: str) -> str:
    if len(t) == 4:
        return t
    return t[8:12] if len(t) >= 12 else ""


def _trading_minutes(t1: str, t2: str) -> Optional[int]:
    """两个 HHMM 之间扣掉午休的分钟数。"""
    try:
        a = datetime.strptime(t1, "%H%M"); b = datetime.strptime(t2, "%H%M")
        mins = int((b - a).total_seconds() // 60)
        if mins <= 0: return mins
        # 扣除 11:30-13:00
        lunch = 0
        if a.hour < 12 and b.hour >= 13:
            lunch = 90
        elif a.hour == 11 and b.hour >= 13:
            lunch = 90
        return mins - lunch
    except Exception:
        return None


def _limit_price(pre: float, symbol: str) -> float:
    code = re.sub(r"\D", "", str(symbol or ""))
    pct = 0.20 if code.startswith(("300", "301", "688", "689")) else BOARD_PCT
    try:
        val = Decimal(str(pre)) * (Decimal("1") + Decimal(str(pct)))
        return float(val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except Exception:
        return 0.0


def _vwap(snap: Dict[str, Any]) -> float:
    q = snap.get("quote") or {}
    v = _f(q.get("average"))
    if v > 0: return v
    vol = _f(q.get("vol")); amt = _f(q.get("amount"))
    return (amt * 100.0 / vol) if vol > 0 and amt > 0 else 0.0


def _cutoff_hm(snap: Dict[str, Any]) -> str:
    vals = []
    for k in ("decision_time", "data_cutoff"):
        h = _hm(_norm_time(snap.get(k)))
        if h and len(h) == 4:
            vals.append(h)
    return min(vals) if vals else ""


def _bars(snap: Dict[str, Any]) -> List[Dict[str, Any]]:
    cutoff = _cutoff_hm(snap)
    asof = re.sub(r"\D", "", str(snap.get("asof") or ""))[:8]
    out = []
    for b in snap.get("bars_5m") or []:
        try:
            t = _norm_time(b.get("time"))
            if len(t) < 12: continue
            if asof and t[:8] != asof: continue
            if cutoff and _hm(t) > cutoff: continue
            out.append({"time": t, "open": _f(b.get("open")), "high": _f(b.get("high")),
                        "low": _f(b.get("low")), "close": _f(b.get("close")),
                        "vol": _f(b.get("vol"))})
        except Exception:
            continue
    out.sort(key=lambda x: x["time"])
    # 末桶未完成：等于 cutoff 的桶剔除
    if out and cutoff and _hm(out[-1]["time"]) == cutoff:
        out = out[:-1]
    return out


def _local_highs(bars: List[Dict[str, Any]]) -> List[int]:
    idx = []
    for i in range(len(bars)):
        lo, hi = max(0, i - 2), min(len(bars), i + 3)
        h = bars[i]["high"]
        if h >= max(x["high"] for x in bars[lo:hi]):
            idx.append(i)
    return idx


def _next_push2(bars: List[Dict[str, Any]], lh: List[int], i1: int) -> Optional[int]:
    """pullback 后的下一个冲高“簇”：取簇内最高点，遇到 1% 回落结束。"""
    later = [i for i in lh if i > i1]
    if not later: return None
    start = later[0]
    best_i = start
    for i in range(start + 1, min(len(bars), start + 8)):
        if bars[i]["high"] > bars[best_i]["high"]:
            best_i = i
        if bars[i]["low"] < bars[best_i]["high"] * 0.99:
            break
    return best_i


def build_features(snap: Dict[str, Any]) -> Dict[str, Any]:
    bars = _bars(snap); q = snap.get("quote") or {}
    px = _f(q.get("current")); pre = _f(q.get("pre_close"))
    cost = _f((snap.get("position") or {}).get("avg_cost"))
    vwap = _vwap(snap)
    lim = _limit_price(pre, snap.get("symbol", "")) if pre > 0 else 0.0
    warnings = list(snap.get("warnings") or [])
    high_q = _f(q.get("high"))
    high_all = max([high_q] + [b["high"] for b in bars]) if bars or high_q else 0.0
    sealed = bool(lim > 0 and px >= lim - 0.001 and high_all >= lim - 0.001)
    open_count = 0
    if lim > 0:
        open_count = sum(1 for b in bars if b["high"] >= lim - 0.001 and b["low"] < lim - 0.001)
    feats: Dict[str, Any] = {
        "price": px, "pre_close": pre, "vwap": round(vwap, 3),
        "day_gain_pct": round((px / pre - 1) * 100, 2) if pre else 0.0,
        "pnl_pct": round((px / cost - 1) * 100, 2) if cost else 0.0,
        "board": {"limit_price": lim, "is_sealed": sealed, "open_count": open_count,
                  "refilled": bool(sealed and open_count > 0)},
        "push1": None, "pullback": None, "push2": None,
        "push2_vs_push1_pct": None, "minutes_since_push2": None,
        "bars_since_push2": 0, "vol_ratio_after": None,
        "vwap_break": False, "accel_end": False, "pattern": "none",
        "warnings": warnings,
    }
    if px <= 0:
        feats["warnings"].append("no_current_price")
        return feats
    if not _cutoff_hm(snap):
        feats["warnings"].append("bad_or_missing_cutoff")
    if len(bars) < 8:
        feats["warnings"].append("bars_too_short")
        return feats
    lh = _local_highs(bars)
    morning = [i for i in lh if "0930" <= _hm(bars[i]["time"]) <= "1030"]
    if not morning:
        morning = [i for i in range(len(bars)) if "0930" <= _hm(bars[i]["time"]) <= "1030"]
        feats["warnings"].append("push1_fallback")
    if not morning:
        feats["warnings"].append("no_morning_window")
        return feats
    i1 = morning[0]
    p1 = bars[i1]["high"]
    feats["push1"] = {"time": bars[i1]["time"], "price": p1}
    i2 = _next_push2(bars, lh, i1)
    if i2 is None:
        feats["warnings"].append("no_push2")
        return feats
    seg = bars[i1 + 1:i2]
    if not seg:
        feats["warnings"].append("no_pullback")
        return feats
    pb = min(seg, key=lambda b: b["low"])
    feats["pullback"] = {"time": pb["time"], "low": pb["low"],
                         "retrace_pct": round((p1 - pb["low"]) / p1 * 100, 2) if p1 else None}
    p2 = bars[i2]["high"]
    feats["push2"] = {"time": bars[i2]["time"], "price": p2,
                      "vs_push1_pct": round((p2 / p1 - 1) * 100, 2) if p1 else None}
    feats["push2_vs_push1_pct"] = feats["push2"]["vs_push1_pct"]
    after = bars[i2 + 1:]
    feats["bars_since_push2"] = len(after)
    if after:
        feats["minutes_since_push2"] = _trading_minutes(_hm(bars[i2]["time"]), _hm(after[-1]["time"]))
    if not after or len(after) < 6:
        return feats
    if any(b["high"] > p2 * 1.001 for b in after):
        feats["warnings"].append("new_high_after_push2")
        return feats
    v_before = sum(b["vol"] for b in bars[max(0, i2 - 6):i2]) / max(1, min(6, i2))
    v_after = sum(b["vol"] for b in after[-6:]) / 6
    feats["vol_ratio_after"] = round(v_after / v_before, 3) if v_before else None
    vwap_break = bool(vwap > 0 and px < vwap * 0.995)
    rounds_below = sum(1 for b in after[-2:] if vwap > 0 and b["close"] < vwap * 0.995)
    feats["vwap_break"] = bool(vwap_break or rounds_below >= 2)
    c_ok = bool(feats["vwap_break"] or px < p2 * 0.99)
    b_ok = feats["vol_ratio_after"] is not None and feats["vol_ratio_after"] < 0.8
    if b_ok and c_ok and not sealed:
        feats["accel_end"] = True
        feats["pattern"] = ("accel_end_2nd_push_failed" if p2 <= p1 * 1.001
                            else "accel_end_after_break")
    return feats


def _volume(decision: str, pos: Dict[str, Any]) -> tuple:
    sellable = int(_f(pos.get("sellable")))
    budget = int(_f(pos.get("remaining_cycle_budget")))
    if decision == "exit_all":
        vol = sellable
    elif decision == "reduce_half":
        vol = min(sellable // 2, budget)
        vol = (vol // 100) * 100
    else:
        vol = 0
    if vol < 100:
        return 0, sellable, "no_sellable_or_budget"
    return vol, sellable, ""


def evaluate(snap: Dict[str, Any]) -> Dict[str, Any]:
    f = build_features(snap)
    pos = snap.get("position") or {}
    decision, signal, reason = "hold", "none", ""
    if f["price"] <= 0:
        return {"decision": "hold", "signal": "none", "reason": "no_current_price",
                "volume": 0, "sellable": int(_f(pos.get("sellable"))), "blocked_reason": "no_price",
                "features": f, "confidence": 0.0, "needs_agent": True,
                "warnings": f.get("warnings") or []}
    # 板上减半优先
    thr = 19.5 if any(re.sub(r"\D", "", str(snap.get("symbol", ""))).startswith(x) for x in ("300", "301", "688", "689")) else 9.5
    if f["day_gain_pct"] >= thr and f["pnl_pct"] >= 3 and not pos.get("board_half_done"):
        decision, signal, reason = "reduce_half", "board_half", "板上减半"
    # 压力位减半
    if decision == "hold":
        for lv in snap.get("levels") or []:
            p = _f(lv.get("price")); typ = str(lv.get("type") or "")
            if typ in ("resistance", "pressure", "prior_high") and f["price"] >= p and f["pnl_pct"] > 0:
                decision, signal, reason = "reduce_half", "pressure_half", "触及压力位 %.2f" % p
                break
    # 加速结束
    if decision == "hold" and f.get("accel_end"):
        decision, signal, reason = "exit_all", "accel_end", f.get("pattern")
    # 跌破支撑（有现价才判）
    if decision == "hold":
        for lv in snap.get("levels") or []:
            p = _f(lv.get("price")); typ = str(lv.get("type") or "")
            if typ in ("support", "prior_low", "platform_low") and f["price"] < p:
                decision, signal, reason = "exit_all", "support_break", "跌破支撑 %.2f" % p
                break
    vol, sellable, blocked = _volume(decision, pos)
    if decision != "hold" and vol <= 0:
        decision, signal, reason = "hold", signal + "_blocked", blocked
    return {"decision": decision, "signal": signal, "reason": reason,
            "volume": vol, "sellable": sellable, "blocked_reason": blocked,
            "features": f,
            "confidence": 0.6 if decision != "hold" else 0.0,
            "needs_agent": bool(f.get("board", {}).get("open_count") and not f.get("board", {}).get("is_sealed")),
            "warnings": f.get("warnings") or []}
