# -*- coding: utf-8 -*-
"""point_engine.py — 指数/个股点位引擎（v3，不硬编码点位）。

设计依据：`docs/wolf-stop-loss-ledger.md` 的 D 组 + `docs/plan-breakdown-stop-loss-v2.md` §3.4。
狼大的点位不是一张固定表，而是每天用下面这些方法算出来的一组合流位：

  · 缺口上下沿（2025-12-08「缺口 3922-3931」）
  · 趋势线 / 日线·周线 BOLL 中轨（2025-06-23「3373 是趋势线也是 BOLL 中轨」）
  · M/W/三重顶颈线（2025-06-23「3330-3334 M 顶最后防守」）
  · 前高/前低/平台（2025-09-17「3828 是底，3856-3830 弱，3861 强」）
  · 对称/区间抬高（2025-10-17「3936→3674；4006→3731」）
  · 测量移动（2026-08-17「3966-3903=60 点，翻倍→4000-3888」）
  · 黄金分割 0.382/0.5/0.618（2026-02-26「=E3-(E3-D3)*0.382」）
  · 成交密集/成本区（2026-04-10「3930 附近最低成本」）
  · 整数关（2026-01-22「4100 中轴，4141 压力」）
  · 波浪锚点（wave_state 的 4-3 低，非价格公式）

本模块只算点位，不判动作；动作在 `wolf_stop_layer.py`。

PIT：调用方传 `bars` 时**不要包含当日**（用上一交易日及以前算点位），再拿当日收盘去比。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# break_levels 是共享结构位计算器（不是旧 BreakStop 策略）；v3 point_engine 依赖，勿删
from app.services import break_levels as BL

TYPE_WEIGHT = {
    "gap_lower": 0.22, "trendline": 0.20, "neckline": 0.20, "boll_mid": 0.16,
    "ma60": 0.14, "ma144": 0.14, "ma200": 0.14, "platform_low_20": 0.18,
    "platform_low_60": 0.20, "prior_day_low": 0.10, "prior_day_high": 0.10,
    "round_number": 0.08, "cost_cluster": 0.16, "retracement": 0.14,
    "today_high": 0.10, "today_low": 0.10, "ma20": 0.16, "round10": 0.08,
    "prior_day_low": 0.12, "prior_day_high": 0.12,
    "filled_gap_lower": 0.14, "filled_gap_upper": 0.14,
    "measured_move": 0.14, "range_symmetry": 0.12, "wave_anchor": 0.22,
}


def _f(x: Any, d: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except (TypeError, ValueError):
        return d


def _date(x: Any) -> str:
    return "".join(c for c in str(x or "") if c.isdigit())[:8]


def _norm_bars(bars: Iterable[Any]) -> List[Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in bars or []:
        if isinstance(r, dict):
            d = _date(r.get("trade_date") or r.get("date"))
            c = _f(r.get("close"))
            h = _f(r.get("high"), c) or c
            lo = _f(r.get("low"), c) or c
            v = _f(r.get("vol") or r.get("volume") or r.get("amount"))
        else:
            try:
                d = _date(r[0]); c = _f(r[4]); h = _f(r[2], c) or c
                lo = _f(r[3], c) or c
                v = _f(r[5]) if len(r) > 5 else 0.0
            except (TypeError, IndexError):
                continue
        if len(d) == 8 and c > 0:
            out[d] = {"date": d, "open": _f(r.get("open"), c) if isinstance(r, dict) else c,
                      "high": max(h, c), "low": min(lo, c), "close": c, "vol": v}
    return [out[d] for d in sorted(out)]


def _mk(typ: str, price: float, asof: str, source: str = "computed",
        touches: int = 1, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"type": typ, "price": round(float(price), 4), "asof": asof,
            "source": source, "touches": int(touches or 1), "meta": dict(meta or {}),
            "role": "support"}


def _retracement(bars: List[Dict[str, Any]], asof: str) -> List[Dict[str, Any]]:
    """最近一段摆动高低点的 0.382/0.5/0.618。"""
    closes = [b["close"] for b in bars]
    if len(closes) < 20:
        return []
    k = 3
    lows = [(i, v) for i, v in BL._local_extrema([b["low"] for b in bars], k, "low")]
    highs = [(i, v) for i, v in BL._local_extrema([b["high"] for b in bars], k, "high")]
    if not lows or not highs:
        return []
    lo_i, lo_v = lows[-1]
    hi_i, hi_v = highs[-1]
    if hi_v <= lo_v:
        return []
    rng = hi_v - lo_v
    out: List[Dict[str, Any]] = []
    for pct in (0.382, 0.5, 0.618):
        if lo_i < hi_i:      # 上涨段 → 回撤支撑
            p = hi_v - rng * pct
        else:                # 下跌段 → 反弹压力
            p = lo_v + rng * pct
        out.append(_mk("retracement", p, asof, touches=1,
                       meta={"pct": pct, "swing_low": lo_v, "swing_high": hi_v,
                             "direction": "up" if lo_i < hi_i else "down"}))
    return out


def _measured_move(bars: List[Dict[str, Any]], asof: str, close: float) -> List[Dict[str, Any]]:
    """最近一段幅度 ×1/×1.618/×2 投影（2026-08-17 的 60→120 点算法）。"""
    highs = BL._local_extrema([b["high"] for b in bars], 3, "high")
    lows = BL._local_extrema([b["low"] for b in bars], 3, "low")
    if not highs or not lows:
        return []
    hi = highs[-1][1]; lo = lows[-1][1]
    rng = hi - lo
    if rng <= 0:
        return []
    out: List[Dict[str, Any]] = []
    for mult, typ in ((1.0, "measured_move"), (1.618, "range_symmetry"), (2.0, "measured_move")):
        for p in (hi - rng * mult, lo + rng * mult):
            if p <= 0 or abs(p - close) / close > 0.15:
                continue
            out.append(_mk(typ, p, asof, meta={"range": round(rng, 2), "mult": mult}))
    return out


def _prior_levels(bars: List[Dict[str, Any]], asof: str, close: float) -> List[Dict[str, Any]]:
    """前一日高/低 + 已回补缺口（缺口边界仍有角色，不能因“已回补”就丢掉）。"""
    if len(bars) < 2 or close <= 0:
        return []
    out: List[Dict[str, Any]] = []
    prev = bars[-2]
    for typ, val in (("prior_day_low", _f(prev.get("low"))), ("prior_day_high", _f(prev.get("high")))):
        if val > 0 and abs(val - close) / close <= 0.05:
            out.append(_mk(typ, val, asof, touches=1))
    # 近 60 根里已回补的缺口：上跳缺口下沿=前高，下跳缺口上沿=前低
    for j in range(max(1, len(bars) - 60), len(bars)):
        a, b = bars[j - 1], bars[j]
        if _f(b["low"]) > _f(a["high"]):          # 上跳
            lower, upper = _f(a["high"]), _f(b["low"])
            filled = any(_f(x["low"]) <= lower for x in bars[j + 1:]) if j + 1 < len(bars) else False
            if filled:
                for typ, val in (("filled_gap_lower", lower), ("filled_gap_upper", upper)):
                    if val > 0 and abs(val - close) / close <= 0.05:
                        out.append(_mk(typ, val, asof, touches=1, meta={"gap_date": b.get("date")}))
        elif _f(b["high"]) < _f(a["low"]):        # 下跳
            lower, upper = _f(b["high"]), _f(a["low"])
            filled = any(_f(x["high"]) >= upper for x in bars[j + 1:]) if j + 1 < len(bars) else False
            if filled:
                for typ, val in (("filled_gap_lower", lower), ("filled_gap_upper", upper)):
                    if val > 0 and abs(val - close) / close <= 0.05:
                        out.append(_mk(typ, val, asof, touches=1, meta={"gap_date": b.get("date")}))
    return out


def _today_levels(bars: List[Dict[str, Any]], asof: str, close: float) -> List[Dict[str, Any]]:
    """当日最高/最低 + MA20 + 十位整数关（补齐他常念的 3370/3388 这类位）。"""
    if not bars or close <= 0:
        return []
    b = bars[-1]
    out: List[Dict[str, Any]] = []
    for typ, val in (("today_high", _f(b.get("high"))), ("today_low", _f(b.get("low")))):
        if val > 0 and abs(val - close) / close <= 0.05:
            out.append(_mk(typ, val, asof, touches=1))
    if len(bars) >= 20:
        ma20 = sum(x["close"] for x in bars[-20:]) / 20.0
        if ma20 > 0 and abs(ma20 - close) / close <= 0.05:
            out.append(_mk("ma20", ma20, asof, touches=2))
    for anchor in (close, _f(b.get("high")), _f(b.get("low"))):
        r = round(anchor / 10.0) * 10.0
        if r > 0 and abs(r - close) / close <= 0.05:
            out.append(_mk("round10", r, asof, touches=1))
    return out


def _cost_cluster(bars: List[Dict[str, Any]], asof: str, close: float) -> List[Dict[str, Any]]:
    """最近 60 根的成交密集区（按价格分 20 桶，取量最大的两桶）。"""
    win = bars[-60:]
    if len(win) < 20 or close <= 0:
        return []
    step = close * 0.01
    if step <= 0:
        return []
    buckets: Dict[int, float] = {}
    for b in win:
        mid = (b["high"] + b["low"] + b["close"]) / 3.0
        idx = int(round(mid / step))
        buckets[idx] = buckets.get(idx, 0.0) + max(b["vol"], 1.0)
    top = sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)[:2]
    return [_mk("cost_cluster", idx * step, asof,
                touches=2, meta={"vol": round(v, 2)})
            for idx, v in top if idx * step > 0]


def _dedup(levels: List[Dict[str, Any]], tol: float = 0.002) -> List[Dict[str, Any]]:
    """只合并**同类型**的近似位；不同类型即使很近也保留（3370 的 today_high/ma20/neckline 是不同结构）。"""
    out: List[Dict[str, Any]] = []
    for lv in levels:
        dup = None
        for e in out:
            if e.get("type") != lv.get("type"):
                continue
            if abs(e["price"] - lv["price"]) / max(e["price"], 1e-9) <= tol:
                dup = e; break
        if dup is None:
            out.append(lv)
        else:
            dup["touches"] = max(dup.get("touches", 1), lv.get("touches", 1)) + 1
            dup["meta"].setdefault("merged", []).append(lv["price"])
    return out


STRUCT_RANK = {
    "swing_low": 3, "neckline": 3, "measured_move": 3, "ma20": 3, "ma34": 3,
    "ma60": 3, "platform_low_20": 3, "platform_low_60": 3,
    "prior_day_low": 2, "prior_day_high": 2, "ma13": 2, "ma5": 2,
    "round10": 2, "round_number": 2, "cost_cluster": 2, "retracement": 2,
    "range_symmetry": 2, "today_high": 1, "today_low": 1,
    "gap_lower": 1, "gap_upper": 1, "filled_gap_lower": 1, "filled_gap_upper": 1,
}


def _score(levels: List[Dict[str, Any]], close: float) -> None:
    for lv in levels:
        conf = 0.0
        for other in levels:
            if other is lv:
                continue
            if abs(other["price"] - lv["price"]) / max(lv["price"], 1e-9) <= 0.005:
                conf += 1
        lv["confluence"] = int(conf)
        lv["distance_pct"] = round((lv["price"] - close) / close * 100.0, 2) if close else None
        w = TYPE_WEIGHT.get(lv["type"], 0.10)
        lv["confidence"] = round(min(1.0, 0.30 + 0.12 * min(conf, 4)
                                     + 0.08 * min(lv["touches"], 3) + w), 3)
        lv["structure_rank"] = STRUCT_RANK.get(lv["type"], 1)


def compute_points(bars: Iterable[Any], asof: Optional[str] = None,
                   domain: str = "index", symbol: str = "",
                   anchors: Optional[Sequence[Dict[str, Any]]] = None,
                   config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """计算点位；`bars` 应为**上一交易日及以前**（PIT）。"""
    bs = _norm_bars(bars)
    if asof:
        a = _date(asof)
        bs = [b for b in bs if b["date"] <= a]
    if not bs:
        return {"asof": None, "close": None, "points": [], "supports": [],
                "resistances": [], "data_quality": {"insufficient": True}}
    close = bs[-1]["close"]
    asof_actual = bs[-1]["date"]
    base = BL.compute_levels(bs, asof=asof_actual, domain=domain, symbol=symbol,
                             anchors=anchors, config=config)
    points = list(base.get("levels") or [])
    points += _retracement(bs, asof_actual)
    points += _measured_move(bs, asof_actual, close)
    points += _cost_cluster(bs, asof_actual, close)
    points += _today_levels(bs, asof_actual, close)
    points += _prior_levels(bs, asof_actual, close)
    for p in points:
        p.setdefault("source", "computed")
        p["role"] = "support" if _f(p["price"]) <= close else "resistance"
    points = _dedup(points)
    _score(points, close)
    # 已跌破/仍在支撑：给 agent 一个可计算事实，不替代角色判断
    for p in points:
        price = _f(p.get("price"))
        if price <= 0:
            continue
        if price <= close:
            p["state"] = "holding"
            p["broken_days"] = 0
            continue
        last_above = None
        for j in range(len(bs) - 1, max(-1, len(bs) - 21), -1):
            if _f(bs[j]["close"]) > price:
                last_above = j
                break
        if last_above is not None and len(bs) - 1 - last_above <= 5:
            p["state"] = "broken"
            p["broken_days"] = len(bs) - 1 - last_above
            p["last_above_date"] = bs[last_above]["date"]
        else:
            p["state"] = "resistance"
            p["broken_days"] = None
    points.sort(key=lambda x: (-x["confidence"], -x["price"]))
    return {"asof": asof_actual, "close": close, "points": points,
            "supports": [p for p in points if p["role"] == "support"],
            "resistances": [p for p in points if p["role"] == "resistance"],
            "data_quality": base.get("data_quality") or {}}


def key_levels(result: Dict[str, Any], n: int = 3,
               max_distance_pct: float = 8.0) -> List[Dict[str, Any]]:
    """选“当天的关键位”：现价下方、距离≤8%、按 confidence 取前 n，价格去重。"""
    close = _f(result.get("close"))
    cands = [p for p in result.get("supports", [])
             if p.get("distance_pct") is not None
             and -max_distance_pct <= float(p["distance_pct"]) <= 0.5]
    cands.sort(key=lambda x: (-_f(x.get("confidence")), -_f(x.get("price"))))
    out: List[Dict[str, Any]] = []
    for p in cands:
        if any(abs(p["price"] - e["price"]) / max(e["price"], 1e-9) < 0.004 for e in out):
            continue
        out.append(p)
        if len(out) >= n:
            break
    return out


# ── 指数大级别破位（兼容原 t_index_break 的调用口径；2026-10-23 迁入）──────────
import os as _os

SWITCH = str(_os.getenv("WOLF_INDEX_BREAKDOWN", "0")).strip().lower() in ("1", "true", "yes", "on")
BIG_LINES = tuple(int(x) for x in (_os.getenv("WOLF_IDX_BREAK_LINES", "60,144,200").split(",")))
MIN_BROKEN = int(_os.getenv("WOLF_IDX_BREAK_MIN", "2"))
CONFIRM_DAYS = int(_os.getenv("WOLF_IDX_BREAK_DAYS", "1"))
_IDX_CACHE: Dict[str, List[tuple]] = {}


def enabled() -> bool:
    return bool(SWITCH)


def _index_series(path: str = "") -> List[tuple]:
    """上证收盘序列 [(YYYYMMDD, close)]；JSON 优先，CSV 兜底。"""
    root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__)))))
    p = path or _os.getenv("WOLF_BREAK_INDEX_JSON",
                           _os.path.join(root, "data", "index_daily_000001.json"))
    if p in _IDX_CACHE:
        return _IDX_CACHE[p]
    out: List[tuple] = []
    try:
        import json as _j
        d = _j.load(open(p, encoding="utf-8"))
        if isinstance(d, list):
            for r in d:
                try:
                    out.append((str(r.get("trade_date") or r.get("date") or "").replace("-", ""),
                                float(r.get("close") or 0)))
                except (TypeError, ValueError):
                    continue
    except Exception:
        out = []
    if not out:
        try:
            import csv as _csv
            for r in _csv.DictReader(open(p, encoding="utf-8")):
                try:
                    out.append((str(r["trade_date"]).replace("-", ""), float(r["close"])))
                except (KeyError, TypeError, ValueError):
                    continue
        except Exception:
            out = []
    out = [(d, c) for d, c in out if len(d) == 8 and c > 0]
    out.sort()
    _IDX_CACHE[p] = out
    return out


def index_close(day: str) -> Optional[float]:
    ser = _index_series()
    tgt = str(day or "").replace("-", "")
    for d, c in reversed(ser):
        if d <= tgt:
            return c
    return None


def index_mas(day: str, lines: Optional[tuple] = None) -> Dict[int, Optional[float]]:
    ser = _index_series(); want = tuple(lines or BIG_LINES)
    tgt = str(day or "").replace("-", "")
    idx = None
    for i in range(len(ser) - 1, -1, -1):
        if ser[i][0] <= tgt:
            idx = i; break
    if idx is None:
        return {n: None for n in want}
    return {n: (round(sum(c for _, c in ser[idx + 1 - n:idx + 1]) / n, 2)
                if idx + 1 >= n else None) for n in want}


def index_breakdown(day: str, lines: Optional[tuple] = None,
                    min_broken: Optional[int] = None,
                    confirm_days: Optional[int] = None) -> Dict[str, Any]:
    """收盘跌破大级别 MA 中至少 N 根，并连续 D 日确认。"""
    ser = _index_series(); want = tuple(lines or BIG_LINES)
    need = MIN_BROKEN if min_broken is None else int(min_broken)
    days = max(1, CONFIRM_DAYS if confirm_days is None else int(confirm_days))
    tgt = str(day or "").replace("-", "")
    pos = None
    for i in range(len(ser) - 1, -1, -1):
        if ser[i][0] <= tgt:
            pos = i; break
    if pos is None:
        return {"broken": False, "below": [], "above": [], "close": None,
                "mas": {}, "streak": 0, "reason": "无指数数据（fail-open）"}
    below: List[int] = []; above: List[int] = []; streak = 0
    for k in range(days):
        i = pos - k
        if i < 0:
            break
        _, close = ser[i]
        mas = {n: (sum(c for _, c in ser[i + 1 - n:i + 1]) / n if i + 1 >= n else None)
               for n in want}
        bl = [n for n in want if mas.get(n) and close < mas[n]]
        if k == 0:
            below, above = bl, [n for n in want if mas.get(n) and close >= mas[n]]
        if len(bl) >= need:
            streak += 1
        else:
            break
    close = ser[pos][1]; mas = index_mas(day, want)
    broken = streak >= days
    return {"broken": broken, "below": below, "above": above, "close": close,
            "mas": mas, "streak": streak,
            "reason": ("收盘 %.2f 跌破大级别 %s 中 %d 根（≥%d）且连续 %d 日 ⇒ 破位"
                       % (close, "/".join("MA%d" % n for n in want), len(below), need, streak))
                      if broken else
                      ("收盘 %.2f 未达破位（跌破 %s，需 ≥%d 根，连续 %d 日）"
                       % (close, below or "无", need, days))}
