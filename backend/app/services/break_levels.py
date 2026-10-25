# -*- coding: utf-8 -*-
"""break_levels.py — 破位级别注册表（BreakStop v1 / P1）。

纯函数模块：**只算事实，不做判断、不读库、不下单**。
输入日线 bars（dict 或 tuple 均可）+ asof，输出类型化 level 列表，供 `break_detector` /
`break_policy` / `break_stop_service` 消费。

设计约束（来自 `docs/plan-breakdown-stop-loss.md` §3.3 与 `docs/ai-agent-lessons-from-b-rebound.md`）：
  · **PIT**：只用 `trade_date <= asof` 的 bar；asof 之后的数据一律不可见。
  · **语义键**：`level_id = symbol:type:asof:anchor`，不用浮点价格做唯一键。
  · **可解释**：每个 level 带 type / price / asof / priority / source / touches / meta。
  · **数据不足不猜**：bar 不够 → 返回 `data_quality.insufficient=True`，level 为空或只给可算的。
  · **口径标注**：close-only 数据（2025-2026 指数 CSV）用 close 代 high/low，并在
    `data_quality.ohlc_complete=False` 标明，禁止把它当完整 OHLC 做影线判断。

level 类型（v1）：
  platform_low_20 / platform_low_60     20/60 日平台低（含触碰次数）
  swing_low / swing_high                摆动低/高（k=3，±0.6% 合并）
  trendline                             13/34/60 日线性回归下轨（斜率<0）
  neckline                              W 底/M 顶颈线（简单摆动点配对）
  gap_lower                             最近未回补缺口的下沿
  ma5/13/34/60/144/200                  均线
  boll_mid                              日线 BOLL 中轨（=MA20，单独标注）
  prior_day_high                        顶部阶段“上一日最高”支撑
  prior_day_low                         前一日低点（“不破就持有 / 放量破+3×15min不收回减仓”）
  swing_low_minus_3                     建仓锚点 × (1-3%)（狼大 2026-03-05）
  round_number                          整数关（指数 50 点、个股自适应）
  wave_anchor                           外部结构锚点（如 3764.15，来自 wave_state）

优先级（base + domain bonus）：
  index +30 / sector +15 / stock +0；平台低 60/65、趋势线 55、颈线 55、缺口 50、
  MA 40、BOLL 35、前高 30、整数 20、外部锚点 95。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULTS: Dict[str, Any] = {
    "swing_k": 3,
    "merge_tol": 0.006,
    "platform_windows": (20, 60),
    "ma_windows": (5, 13, 34, 60, 144, 200),
    "trend_windows": (13, 34, 60),
    "gap_lookback": 60,
    "gap_min_pct": 0.002,
    "min_touches": 1,
    "trend_min_r2": 0.45,
    "max_level_distance_pct": 15.0,
    "include_prior_high": True,
    "include_prior_low": True,
    "boll_window": 20,
    "swing_low_win": 13,
    "swing_low_pct": 3.0,
    "round_step": None,
    "entry_anchor": None,
}

_DOMAIN_BONUS = {"index": 30, "sector": 15, "stock": 0}
_BASE_PRIORITY = {
    "platform_low_20": 60,
    "platform_low_60": 65,
    "swing_low": 45,
    "swing_high": 30,
    "trendline": 55,
    "neckline": 55,
    "gap_lower": 50,
    "ma5": 40,
    "ma13": 40,
    "ma34": 40,
    "ma60": 40,
    "ma144": 40,
    "ma200": 40,
    "boll_mid": 35,
    "prior_day_high": 30,
    "prior_day_low": 35,
    "swing_low_minus_3": 30,
    "round_number": 20,
    "wave_anchor": 95,
}


def _f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _norm_date(x: Any) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())[:8]


def _cfg(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    if config:
        cfg.update({k: v for k, v in config.items() if v is not None})
    return cfg


def _norm_bars(bars: Iterable[Any], asof: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """归一化 bars；只保留 asof（含）之前的已完成日线。"""
    tgt = _norm_date(asof)
    out: Dict[str, Dict[str, Any]] = {}
    for r in bars or []:
        if isinstance(r, dict):
            d = _norm_date(r.get("trade_date") or r.get("date"))
            close = _f(r.get("close"))
            o = _f(r.get("open"), close)
            hi = _f(r.get("high"), close)
            lo = _f(r.get("low"), close)
            vol = _f(r.get("vol") or r.get("volume"))
            amt = _f(r.get("amount"))
        else:
            try:
                d = _norm_date(r[0])
                o, hi, lo, close = _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4])
                amt = _f(r[5]) if len(r) > 5 else 0.0
                vol = _f(r[6]) if len(r) > 6 else 0.0
            except (TypeError, IndexError):
                continue
        if len(d) != 8 or close <= 0:
            continue
        if tgt and d > tgt:
            continue
        if hi <= 0:
            hi = close
        if lo <= 0:
            lo = close
        # 一些指数 CSV 只有 close；用 close 代 high/low，并标记
        out[d] = {
            "date": d, "open": o, "high": max(hi, close), "low": min(lo, close),
            "close": close, "vol": vol, "amount": amt,
        }
    clean = [out[d] for d in sorted(out)]
    ohlc_complete = all(
        abs(b["high"] - b["close"]) > 1e-9 or abs(b["low"] - b["close"]) > 1e-9
        for b in clean
    ) if clean else False
    warnings: List[str] = []
    if len(clean) < 20:
        warnings.append("bars_lt_20")
    if clean and not ohlc_complete:
        warnings.append("close_only_or_flat_ohlc")
    return clean, {
        "bars": len(clean),
        "asof": clean[-1]["date"] if clean else None,
        "requested_asof": tgt or None,
        "ohlc_complete": bool(ohlc_complete),
        "insufficient": len(clean) < 20,
        "warnings": warnings,
    }


def _ma(vals: Sequence[float], n: int) -> Optional[float]:
    if n <= 0 or len(vals) < n:
        return None
    return sum(vals[-n:]) / float(n)


def _linreg(vals: Sequence[float]) -> Tuple[float, float, float]:
    """最小二乘 y = a + b*x；返回 (a, b, r2)。"""
    n = len(vals)
    if n < 3:
        return 0.0, 0.0, 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(vals) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return my, 0.0, 0.0
    b = sum((x - mx) * (y - my) for x, y in zip(xs, vals)) / sxx
    a = my - b * mx
    ss_tot = sum((y - my) ** 2 for y in vals)
    ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, vals))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return a, b, max(0.0, min(1.0, r2))


def _local_extrema(vals: Sequence[float], k: int, kind: str) -> List[Tuple[int, float]]:
    out: List[Tuple[int, float]] = []
    if len(vals) < 2 * k + 1:
        return out
    for i in range(k, len(vals) - k):
        win = vals[i - k:i + k + 1]
        if kind == "low" and vals[i] <= min(win) and vals[i] < vals[i - 1]:
            out.append((i, vals[i]))
        elif kind == "high" and vals[i] >= max(win) and vals[i] > vals[i - 1]:
            out.append((i, vals[i]))
    return out


def _merge_points(points: List[Tuple[int, float]], tol: float, kind: str,
                  dates: Sequence[str]) -> List[Dict[str, Any]]:
    """把 ±tol 内的摆动点合并成平台；返回 representative。"""
    if not points:
        return []
    ordered = sorted(points, key=lambda p: p[1], reverse=(kind == "high"))
    merged: List[Dict[str, Any]] = []
    for idx, val in ordered:
        if merged and abs(merged[-1]["price"] - val) / max(merged[-1]["price"], 1e-9) <= tol:
            merged[-1]["touches"] += 1
            # low 取更低、high 取更高
            if (kind == "low" and val < merged[-1]["price"]) or (kind == "high" and val > merged[-1]["price"]):
                merged[-1]["price"] = val
                merged[-1]["anchor"] = dates[idx]
            continue
        merged.append({"price": val, "touches": 1, "anchor": dates[idx]})
    return merged


def _lid(symbol: str, type_: str, asof: str, anchor: Any) -> str:
    a = str(anchor or asof).replace(" ", "")
    return f"{symbol or 'NA'}:{type_}:{asof}:{a}"


def _mk_level(symbol: str, domain: str, type_: str, price: float, asof: str,
              anchor: Any, source: str = "bars", touches: int = 1,
              meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base = _BASE_PRIORITY.get(type_, 30)
    bonus = _DOMAIN_BONUS.get(domain, 0)
    return {
        "level_id": _lid(symbol, type_, asof, anchor),
        "symbol": symbol,
        "domain": domain,
        "type": type_,
        "price": round(float(price), 4),
        "asof": asof,
        "priority": int(base + bonus),
        "source": source,
        "touches": int(touches or 1),
        "anchor": str(anchor or asof),
        "meta": dict(meta or {}),
    }


def _platform_level(clean: List[Dict[str, Any]], n: int, symbol: str, domain: str,
                    asof: str, tol: float) -> Optional[Dict[str, Any]]:
    if len(clean) < n:
        return None
    win = clean[-n:]
    lows = [b["low"] for b in win]
    lo = min(lows)
    idx = lows.index(lo)
    touches = sum(1 for x in lows if abs(x - lo) / max(lo, 1e-9) <= tol)
    if touches < 1:
        return None
    return _mk_level(symbol, domain, f"platform_low_{n}", lo, asof,
                     anchor=win[idx]["date"], touches=touches,
                     meta={"window": n, "kind": "platform_low"})


def _trendline_levels(clean: List[Dict[str, Any]], windows: Sequence[int], symbol: str,
                      domain: str, asof: str, min_r2: float,
                      max_dist_pct: float) -> List[Dict[str, Any]]:
    closes = [b["close"] for b in clean]
    out: List[Dict[str, Any]] = []
    for n in windows:
        if len(closes) < n:
            continue
        a, b, r2 = _linreg(closes[-n:])
        if r2 < min_r2 or b >= 0:
            continue
        line = a + b * (n - 1)
        if line <= 0:
            continue
        dist = (closes[-1] - line) / closes[-1] * 100.0
        if dist < -2.0 or dist > max_dist_pct:
            continue
        out.append(_mk_level(
            symbol, domain, "trendline", line, asof,
            anchor=f"w{n}", source="bars", touches=1,
            meta={"window": n, "slope": round(b, 6), "r2": round(r2, 4),
                  "distance_pct": round(dist, 2)},
        ))
    return out


def _neckline_levels(clean: List[Dict[str, Any]], k: int, tol: float,
                     symbol: str, domain: str, asof: str,
                     max_dist_pct: float) -> List[Dict[str, Any]]:
    highs = [b["high"] for b in clean]
    lows = [b["low"] for b in clean]
    dates = [b["date"] for b in clean]
    close = clean[-1]["close"]
    sl = _local_extrema(lows, k, "low")
    sh = _local_extrema(highs, k, "high")
    out: List[Dict[str, Any]] = []
    # W 底：两个相近的摆动低，颈线 = 中间最高
    for p in range(len(sl) - 1):
        i1, v1 = sl[p]
        for i2, v2 in sl[p + 1:]:
            if i2 - i1 < 5:
                continue
            if abs(v1 - v2) / max(v1, v2, 1e-9) > tol * 2.5:
                continue
            neck = max(highs[i1:i2 + 1])
            if neck <= max(v1, v2):
                continue
            if abs(neck - close) / close * 100 > max_dist_pct:
                continue
            out.append(_mk_level(
                symbol, domain, "neckline", neck, asof,
                anchor=f"{dates[i1]}-{dates[i2]}", touches=2,
                meta={"pattern": "W", "low1": v1, "low2": v2},
            ))
            break
    # M 顶：两个相近的摆动高，颈线 = 中间最低（破位=支撑失守）
    for p in range(len(sh) - 1):
        i1, v1 = sh[p]
        for i2, v2 in sh[p + 1:]:
            if i2 - i1 < 5:
                continue
            if abs(v1 - v2) / max(v1, v2, 1e-9) > tol * 2.5:
                continue
            neck = min(lows[i1:i2 + 1])
            if neck >= min(v1, v2):
                continue
            if abs(neck - close) / close * 100 > max_dist_pct:
                continue
            out.append(_mk_level(
                symbol, domain, "neckline", neck, asof,
                anchor=f"{dates[i1]}-{dates[i2]}", touches=2,
                meta={"pattern": "M", "high1": v1, "high2": v2},
            ))
            break
    return out


def _gap_levels(clean: List[Dict[str, Any]], lookback: int, min_pct: float,
                symbol: str, domain: str, asof: str,
                max_dist_pct: float) -> List[Dict[str, Any]]:
    n = len(clean)
    start = max(1, n - lookback)
    best: Optional[Dict[str, Any]] = None
    for i in range(start, n):
        prev, cur = clean[i - 1], clean[i]
        # 向上跳空：今日低 > 昨日高；下沿=昨日高，未回补=之后最低仍 > 下沿
        if cur["low"] > prev["high"] * (1 + min_pct):
            lower = prev["high"]
            if lower <= 0:
                continue
            later = clean[i + 1:]
            if later and min(b["low"] for b in later) <= lower:
                continue
            if abs(lower - clean[-1]["close"]) / clean[-1]["close"] * 100 <= max_dist_pct:
                best = _mk_level(symbol, domain, "gap_lower", lower, asof,
                                 anchor=cur["date"], touches=1,
                                 meta={"direction": "up", "gap_date": cur["date"],
                                       "upper": cur["low"]})
        # 向下跳空：今日高 < 昨日低；下沿=今日高，未回补=之后最高仍 < 上沿
        elif cur["high"] < prev["low"] * (1 - min_pct):
            lower = cur["high"]
            if lower <= 0:
                continue
            later = clean[i + 1:]
            if later and max(b["high"] for b in later) >= prev["low"]:
                continue
            if abs(lower - clean[-1]["close"]) / clean[-1]["close"] * 100 <= max_dist_pct:
                best = _mk_level(symbol, domain, "gap_lower", lower, asof,
                                 anchor=cur["date"], touches=1,
                                 meta={"direction": "down", "gap_date": cur["date"],
                                       "upper": prev["low"]})
    return [best] if best else []


def _round_step(price: float, override: Any = None) -> float:
    if override:
        try:
            return max(float(override), 0.001)
        except (TypeError, ValueError):
            pass
    if price >= 1000:
        return 50.0
    if price >= 100:
        return 10.0
    if price >= 10:
        return 1.0
    if price >= 1:
        return 0.1
    return 0.01


def _round_levels(close: float, step: float, symbol: str, domain: str, asof: str) -> List[Dict[str, Any]]:
    lo = math.floor(close / step) * step
    hi = lo + step
    out = []
    for p in (lo, hi):
        if p <= 0:
            continue
        out.append(_mk_level(symbol, domain, "round_number", p, asof,
                             anchor=f"step{step:g}:{p:.4f}", touches=1,
                             meta={"step": step}))
    return out


def _dedup(levels: List[Dict[str, Any]], tol: float = 0.0015) -> List[Dict[str, Any]]:
    """同 type 且价格几乎相同 → 保留 touches 多/优先级高的。"""
    out: List[Dict[str, Any]] = []
    for lv in levels:
        dup = None
        for e in out:
            if e["type"] == lv["type"] and abs(e["price"] - lv["price"]) / max(e["price"], 1e-9) <= tol:
                dup = e
                break
        if dup is None:
            out.append(lv)
        elif (lv.get("touches", 1), lv.get("priority", 0)) > (dup.get("touches", 1), dup.get("priority", 0)):
            dup.update(lv)
    return out


def _confluence(levels: List[Dict[str, Any]], tol: float = 0.005) -> None:
    """给每个 level 标注附近不同 type 的 level 数（合流强度）。"""
    for lv in levels:
        n = 0
        for other in levels:
            if other is lv or other["type"] == lv["type"]:
                continue
            if abs(other["price"] - lv["price"]) / max(lv["price"], 1e-9) <= tol:
                n += 1
        lv["meta"]["confluence"] = n


def compute_levels(bars: Iterable[Any], asof: Optional[str] = None,
                   domain: str = "stock", symbol: str = "",
                   anchors: Optional[Sequence[Dict[str, Any]]] = None,
                   config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """计算类型化 level。

    Args:
        bars: 日线序列（dict: trade_date/open/high/low/close[/vol/amount] 或 tuple）
        asof: 只用到该日（含）；None = 最后一根
        domain: index | sector | stock
        symbol: 标的代码（用于 level_id）
        anchors: 外部锚点，如 [{"type":"wave_anchor","price":3764.15,"source":"wave_state"}]
        config: 覆盖 DEFAULTS

    Returns:
        {"symbol","domain","asof","close","levels","supports","resistances","data_quality"}
    """
    cfg = _cfg(config)
    clean, quality = _norm_bars(bars, asof)
    out: Dict[str, Any] = {
        "symbol": symbol, "domain": domain, "asof": quality.get("asof"),
        "close": clean[-1]["close"] if clean else None,
        "levels": [], "supports": [], "resistances": [],
        "data_quality": quality,
    }
    if not clean:
        quality["warnings"].append("no_bars")
        return out

    asof_actual = clean[-1]["date"]
    closes = [b["close"] for b in clean]
    levels: List[Dict[str, Any]] = []

    # 平台低
    for n in cfg["platform_windows"]:
        lv = _platform_level(clean, int(n), symbol, domain, asof_actual,
                             float(cfg["merge_tol"]))
        if lv:
            levels.append(lv)

    # 均线
    for n in cfg["ma_windows"]:
        v = _ma(closes, int(n))
        if v and v > 0:
            levels.append(_mk_level(symbol, domain, f"ma{int(n)}", v, asof_actual,
                                    anchor=f"ma{int(n)}", meta={"window": int(n)}))

    # BOLL 中轨（=MA20，单独一条，便于策略引用）
    boll = _ma(closes, int(cfg["boll_window"]))
    if boll and boll > 0:
        levels.append(_mk_level(symbol, domain, "boll_mid", boll, asof_actual,
                                anchor=f"boll{int(cfg['boll_window'])}",
                                meta={"window": int(cfg["boll_window"])}))

    # 摆动低/高
    k = int(cfg["swing_k"])
    dates = [b["date"] for b in clean]
    lows = [b["low"] for b in clean]
    highs = [b["high"] for b in clean]
    for kind, vals, type_ in (("low", lows, "swing_low"), ("high", highs, "swing_high")):
        pts = _local_extrema(vals, k, kind)
        merged = _merge_points(pts, float(cfg["merge_tol"]), kind, dates)
        for m in merged:
            levels.append(_mk_level(symbol, domain, type_, m["price"], asof_actual,
                                    anchor=m["anchor"], touches=m["touches"]))

    # 趋势线
    levels.extend(_trendline_levels(clean, cfg["trend_windows"], symbol, domain,
                                    asof_actual, float(cfg["trend_min_r2"]),
                                    float(cfg["max_level_distance_pct"])))

    # 颈线
    levels.extend(_neckline_levels(clean, k, float(cfg["merge_tol"]), symbol, domain,
                                   asof_actual, float(cfg["max_level_distance_pct"])))

    # 缺口下沿
    levels.extend(_gap_levels(clean, int(cfg["gap_lookback"]), float(cfg["gap_min_pct"]),
                              symbol, domain, asof_actual,
                              float(cfg["max_level_distance_pct"])))

    # “前一日”= 最后一根已完成 bar（asof 当日收盘后，供**下一交易日**使用）。
    # 顶部阶段“上一日最高”支撑（2025-05-13「每一天的支撑位都是上一天的最高位」）
    if cfg.get("include_prior_high", True) and len(clean) >= 1:
        ph = clean[-1]["high"]
        if ph > 0:
            levels.append(_mk_level(symbol, domain, "prior_day_high", ph, asof_actual,
                                    anchor=clean[-1]["date"], meta={"kind": "prior_day_high"}))

    # 前一日低点（2026-02-05「收盘没跌破前一天低点就不减仓」；
    # 2025-12-11「跌破昨天低点超过 3 个 15 分钟没收回 → 反抽减仓」）
    if cfg.get("include_prior_low", True) and len(clean) >= 1:
        pl = clean[-1]["low"]
        if pl > 0:
            levels.append(_mk_level(symbol, domain, "prior_day_low", pl, asof_actual,
                                    anchor=clean[-1]["date"], meta={"kind": "prior_day_low"}))

    # 整数关
    step = _round_step(closes[-1], cfg.get("round_step"))
    levels.extend(_round_levels(closes[-1], step, symbol, domain, asof_actual))

    # 建仓锚点 -3%（狼大 2026-03-05）
    entry_anchor = cfg.get("entry_anchor")
    if entry_anchor is None and anchors:
        for a in anchors:
            if str((a or {}).get("type")) == "entry_anchor":
                entry_anchor = _f((a or {}).get("price"))
                break
    if entry_anchor:
        p = _f(entry_anchor) * (1.0 - float(cfg["swing_low_pct"]) / 100.0)
        if p > 0:
            levels.append(_mk_level(symbol, domain, "swing_low_minus_3", p, asof_actual,
                                    anchor=f"entry{_f(entry_anchor):.4f}",
                                    meta={"entry_anchor": _f(entry_anchor),
                                          "pct": float(cfg["swing_low_pct"])}))

    # 外部锚点（如 wave_anchor 3764.15）
    for a in anchors or []:
        if not isinstance(a, dict):
            continue
        typ = str(a.get("type") or "wave_anchor")
        if typ == "entry_anchor":
            continue
        price = _f(a.get("price"))
        if price <= 0:
            continue
        levels.append(_mk_level(symbol, domain, typ, price, asof_actual,
                                anchor=str(a.get("anchor") or a.get("source") or "external"),
                                source=str(a.get("source") or "external"),
                                touches=int(a.get("touches") or 1),
                                meta={"external": True}))

    levels = _dedup(levels)
    _confluence(levels)
    levels.sort(key=lambda x: (-float(x["price"]), -int(x["priority"])))
    close = closes[-1]
    out["levels"] = levels
    out["supports"] = [lv for lv in levels if lv["price"] <= close * 1.0001]
    out["resistances"] = [lv for lv in levels if lv["price"] > close * 1.0001]
    return out


def nearest_support(result: Dict[str, Any], price: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """现价下方最近的支撑 level（含现价）。"""
    close = _f(price if price is not None else result.get("close"))
    cands = [lv for lv in result.get("levels", []) if _f(lv.get("price")) <= close * 1.0001]
    return max(cands, key=lambda x: (_f(x["price"]), int(x.get("priority", 0)))) if cands else None


def nearest_resistance(result: Dict[str, Any], price: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """现价上方最近的压力 level。"""
    close = _f(price if price is not None else result.get("close"))
    cands = [lv for lv in result.get("levels", []) if _f(lv.get("price")) > close * 1.0001]
    return min(cands, key=lambda x: (_f(x["price"]), -int(x.get("priority", 0)))) if cands else None


def levels_below(result: Dict[str, Any], price: float) -> List[Dict[str, Any]]:
    return [lv for lv in result.get("levels", []) if _f(lv.get("price")) <= _f(price)]


def levels_above(result: Dict[str, Any], price: float) -> List[Dict[str, Any]]:
    return [lv for lv in result.get("levels", []) if _f(lv.get("price")) > _f(price)]
