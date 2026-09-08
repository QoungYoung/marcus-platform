# -*- coding: utf-8 -*-
"""support_resistance.py — 波段支撑/压力位计算（狼大"画线点位/剧本"落地·步骤①，2026-09-08）

思路（对齐狼大语料）：
- 支撑/压力=前期突破位/画线点位/局部峰谷/平台低高（2025-03-31: "这些点位都是之前突破位，
  相反下跌就是支撑位"）；回踩不破=有效，放量破=减。
- 数据：ETF/指数用 tushare fund_daily，个股 pro.daily（自动降级，无需每标的手工画线）。
- 输出最近支撑/压力带：局部谷/峰(5日摆动合并±0.6%) + 20/60日平台低/高 + MA20/60 + 整数刻度。
"""
import os, time, json
from datetime import date
from typing import Dict, List, Optional

_CACHE: Dict[str, dict] = {}      # symbol -> {at, date, bars, levels}
_TTL = 600.0                       # 点位每 10 分钟重算
DATE8 = date.today().strftime("%Y%m%d")


def _norm(symbol: str) -> str:
    s = str(symbol or "").upper().strip()
    if "." in s:
        code, ex = s.split(".", 1)
        return code + "." + ex
    if s[:2] in ("SH", "SZ", "BJ"):
        code, ex = s[2:], s[:2]
    else:
        code = s
        ex = "SH" if code.startswith(("6", "9")) else ("BJ" if code.startswith(("4", "8")) else "SZ")
    return code + "." + ex


def get_daily_bars(symbol: str, n: int = 70) -> List[dict]:
    """日K bars：个股 pro.daily → ETF/指数 fund_daily（自动降级）。返回 [{trade_date,open,high,low,close,vol?}] 升序。"""
    ts = _norm(symbol)
    today = DATE8
    try:
        from datetime import timedelta
        start = (date.today() - timedelta(days=int(n * 1.7) + 15)).strftime("%Y%m%d")
    except Exception:
        start = "20260401"
    try:
        from app.api.market import _get_tushare_pro
        pro = _get_tushare_pro()
        bars = []
        for fn_name in ("daily", "fund_daily"):
            try:
                fn = getattr(pro, fn_name)
                df = fn(ts_code=ts, start_date=start, end_date=today)
                if df is None or df.empty:
                    continue
                recs = df.sort_values("trade_date").to_dict("records")
                if len(recs) > len(bars):
                    bars = [{
                        "trade_date": str(r.get("trade_date")),
                        "open": float(r.get("open") or 0),
                        "high": float(r.get("high") or 0),
                        "low": float(r.get("low") or 0),
                        "close": float(r.get("close") or 0),
                        "vol": float(r.get("vol") or r.get("volume") or 0),
                    } for r in recs]
            except Exception:
                continue
        return bars
    except Exception as e:
        print(f"[SR] get_daily_bars fail {symbol}: {str(e)[:100]}")
        return []


def _local_extrema(bars: List[dict], k: int = 3, kind: str = "low") -> List[float]:
    """窗口 k 摆动谷/峰（左k右k不更低/更高）。"""
    vals = [float(b[kind]) for b in bars]
    out = []
    for i in range(k, len(vals) - k):
        win = vals[i - k:i + k + 1]
        if kind == "low" and vals[i] == min(win) and vals[i] < vals[i - 1]:
            out.append(vals[i])
        elif kind == "high" and vals[i] == max(win) and vals[i] > vals[i - 1]:
            out.append(vals[i])
    return out


def _merge(points: List[float], tol: float) -> List[float]:
    """±tol 内合并成平台（取最极端值），降序去重。"""
    ps = sorted(set(round(p, 4) for p in points if p and p > 0), reverse=True)
    merged = []
    for p in ps:
        if merged and abs(merged[-1] - p) / max(merged[-1], 1e-9) <= tol:
            merged[-1] = min(merged[-1], p)  # 支撑取更低
        else:
            merged.append(p)
    return merged


def compute_levels(symbol: str, force: bool = False) -> Dict[str, List[dict]]:
    """计算支撑/压力带。返回 {"support":[{price,kind,label}], "resistance":[...]}。"""
    global _CACHE
    sym = _norm(symbol)
    now = time.time()
    c = _CACHE.get(sym)
    if not force and c and c.get("date") == DATE8 and now - c.get("at", 0) < _TTL:
        return c["levels"]
    bars = get_daily_bars(sym, 70)
    if len(bars) < 15:
        return {"support": [], "resistance": []}
    closes = [float(b["close"]) for b in bars]
    cur = closes[-1]
    lo20 = min(float(b["low"]) for b in bars[-20:])
    hi20 = max(float(b["high"]) for b in bars[-20:])
    lo60 = min(float(b["low"]) for b in bars[-60:]) if len(bars) >= 60 else lo20
    hi60 = max(float(b["high"]) for b in bars[-60:]) if len(bars) >= 60 else hi20
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else ma20
    # 局部谷/峰
    lows = _merge(_local_extrema(bars, 3, "low"), 0.006)
    highs = _merge(_local_extrema(bars, 3, "high"), 0.006)
    support_candidates = [lo60, lo20] + lows + [ma20, ma60] if cur < ma60 else [lo60, lo20] + lows + [ma20]
    resist_candidates = [hi60, hi20] + highs + [ma20, ma60] if cur >= ma60 else [hi20] + highs + [ma20]
    # 整数刻度（只保留比现价低的支撑 / 高的压力，离现价 <15%）
    tick = 0.01 if cur < 1 else (0.05 if cur < 10 else 0.5)
    s_int = max(0.001, (cur // tick) * tick)
    r_int = (cur // tick + 1) * tick
    support_candidates += [s_int]
    resist_candidates += [r_int]

    def pack(vals, kind):
        out = []
        for v in sorted(set(round(x, 3) for x in vals if x and x > 0), reverse=True):
            if kind == "support" and v > cur * 1.0:
                continue
            if kind == "resistance" and v < cur:
                continue
            if abs(v - cur) / cur > 0.15:
                continue
            out.append({"price": v, "kind": kind, "label": _label(kind, v, cur, lo20, hi20, ma20, ma60)})
        return out[:4]

    levels = {"support": pack(support_candidates, "support"),
              "resistance": pack(resist_candidates, "resistance"),
              "asof": DATE8, "current": round(cur, 3)}
    _CACHE[sym] = {"at": now, "date": DATE8, "levels": levels}
    return levels


def _label(kind: str, v: float, cur: float, lo20: float, hi20: float, ma20: float, ma60: float) -> str:
    tags = []
    if abs(v - lo20) / max(lo20, 1e-9) < 0.01:
        tags.append("20日低")
    elif abs(v - hi20) / max(hi20, 1e-9) < 0.01:
        tags.append("20日高")
    if abs(v - ma20) / max(ma20, 1e-9) < 0.01:
        tags.append("MA20")
    if abs(v - ma60) / max(ma60, 1e-9) < 0.01:
        tags.append("MA60")
    return "、".join(tags) if tags else ("波段低点" if kind == "support" else "波段高点")
