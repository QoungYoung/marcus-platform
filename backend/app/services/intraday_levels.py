# -*- coding: utf-8 -*-
"""intraday_levels.py — 盘中/15min 结构位（日线候选之外）。

用途：狼大很多减仓触发是“前一日低点/盘中位 + 3×15min”，日线候选生成不出来。
本模块只做 15min 聚合与结构位提取，不下结论。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _f(x: Any, d: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def clean_5m(bars5: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """过滤坏行：OHLC 比例异常 / 相对前收跳变 >10%。"""
    rows = []
    prev = None
    for b in sorted(bars5 or [], key=lambda x: str(x.get("trade_time") or x.get("time") or "")):
        o, h, l, c = _f(b.get("open")), _f(b.get("high")), _f(b.get("low")), _f(b.get("close"))
        if min(o, h, l, c) <= 0 or h < l:
            continue
        if h / max(l, 1e-9) > 1.20:
            continue
        if prev and abs(c / prev - 1.0) > 0.10:
            continue
        rows.append(b)
        prev = c
    return rows


def _swings_5m(bars: List[Dict[str, Any]], k: int = 3) -> List[Dict[str, Any]]:
    out = []
    for i in range(k, len(bars) - k):
        if bars[i]["low"] == min(b["low"] for b in bars[i - k:i + k + 1]):
            out.append({"type": "m5_swing_low", "price": bars[i]["low"], "time": bars[i]["trade_time"]})
        if bars[i]["high"] == max(b["high"] for b in bars[i - k:i + k + 1]):
            out.append({"type": "m5_swing_high", "price": bars[i]["high"], "time": bars[i]["trade_time"]})
    return out[-16:]


def agg_15m(bars5: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """5min → 15min（按 :00/:15/:30/:45 对齐）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for b in bars5 or []:
        t = str(b.get("trade_time") or b.get("time") or "")
        if len(t) < 16:
            continue
        hm = t[11:16]
        try:
            hh, mm = int(hm[:2]), int(hm[3:5])
        except ValueError:
            continue
        key = "%s %02d:%02d" % (t[:10], hh, (mm // 15) * 15)
        o, h, l, c = _f(b.get("open")), _f(b.get("high")), _f(b.get("low")), _f(b.get("close"))
        v = _f(b.get("vol") or b.get("volume"))
        if key not in out:
            out[key] = {"time": key, "open": o, "high": h, "low": l, "close": c, "vol": v}
        else:
            x = out[key]
            x["high"] = max(x["high"], h)
            x["low"] = min(x["low"], l)
            x["close"] = c
            x["vol"] += v
    return [out[k] for k in sorted(out)]


def _swings(bars: List[Dict[str, Any]], k: int = 2) -> List[Dict[str, Any]]:
    out = []
    for i in range(k, len(bars) - k):
        if bars[i]["low"] == min(b["low"] for b in bars[i - k:i + k + 1]):
            out.append({"type": "m15_swing_low", "price": bars[i]["low"], "time": bars[i]["time"]})
        if bars[i]["high"] == max(b["high"] for b in bars[i - k:i + k + 1]):
            out.append({"type": "m15_swing_high", "price": bars[i]["high"], "time": bars[i]["time"]})
    return out[-12:]


def compute_intraday_levels(bars5: List[Dict[str, Any]], asof: str, close: float) -> List[Dict[str, Any]]:
    """提取 as-of 当日及前一日的 15min 结构位。"""
    bars5 = clean_5m(bars5)
    b15 = agg_15m(bars5)
    days = sorted({b["time"][:10] for b in b15})
    if not days:
        return []
    today = asof if asof in days else days[-1]
    prior = days[days.index(today) - 1] if days.index(today) > 0 else None
    out: List[Dict[str, Any]] = []

    def add(typ: str, price: float, time: str) -> None:
        if price > 0:
            out.append({"type": typ, "price": round(price, 2), "time": time,
                        "state": "broken" if close < price else "holding"})

    if prior:
        p5 = [b for b in bars5 if str(b.get("trade_time"))[:10] == prior]
        for s in _swings_5m(p5):
            add(s["type"].replace("m5_", "m5_prev_"), s["price"], s["time"])
        pm = [b for b in p5 if "13:00" <= str(b.get("trade_time"))[11:16] <= "15:00"]
        for b in pm:
            if abs(b["high"] - close) / close <= 0.03:
                add("m5_prev_pm_high", b["high"], b["trade_time"])
            if abs(b["low"] - close) / close <= 0.03:
                add("m5_prev_pm_low", b["low"], b["trade_time"])
        pb = [b for b in b15 if b["time"][:10] == prior]
        if pb:
            add("m15_prev_first_low", pb[0]["low"], pb[0]["time"])
            add("m15_prev_first_high", pb[0]["high"], pb[0]["time"])
            add("m15_prev_low", min(b["low"] for b in pb), prior)
            add("m15_prev_high", max(b["high"] for b in pb), prior)
            for s in _swings(pb):
                add(s["type"].replace("m15_", "m15_prev_"), s["price"], s["time"])
    t5 = [b for b in bars5 if str(b.get("trade_time"))[:10] == today]
    for s in _swings_5m(t5):
        add(s["type"].replace("m5_", "m5_today_"), s["price"], s["time"])
    tpm = [b for b in t5 if "13:00" <= str(b.get("trade_time"))[11:16] <= "15:00"]
    for b in tpm:
        if abs(b["high"] - close) / close <= 0.03:
            add("m5_today_pm_high", b["high"], b["trade_time"])
        if abs(b["low"] - close) / close <= 0.03:
            add("m5_today_pm_low", b["low"], b["trade_time"])
    tb = [b for b in b15 if b["time"][:10] == today]
    if tb:
        add("m15_today_first_low", tb[0]["low"], tb[0]["time"])
        add("m15_today_first_high", tb[0]["high"], tb[0]["time"])
        add("m15_today_low", min(b["low"] for b in tb), today)
        add("m15_today_high", max(b["high"] for b in tb), today)
        # VWAP（用 close 近似）
        _v = sum(b["vol"] for b in tb)
        if _v > 0:
            add("m15_today_vwap", sum(b["close"] * b["vol"] for b in tb) / _v, today)
    # 去重（同 type + 价 0.05% 内）
    dedup: List[Dict[str, Any]] = []
    for x in out:
        if any(y["type"] == x["type"] and abs(y["price"] - x["price"]) / max(x["price"], 1e-9) < 0.0001
               for y in dedup):
            continue
        dedup.append(x)
    return dedup
