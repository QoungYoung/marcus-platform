# -*- coding: utf-8 -*-
"""正T信号重构对比回测：A个股挂前日低点 / B个股当日回撤 / C大盘分时急杀 vs 现249(上证整日回撤2-3%)
统一口径：信号日收盘买入 -> T+1/T+2 收盘收益；对照全交易日基线。
数据：data/index_5min_dh.json(上证184天) + data/stock_5min_{5股}.json(197天)
"""
import json, os
import pandas as pd, numpy as np

DATA = os.environ.get("DATA_DIR", "data")
STOCKS = ["603259", "603678", "000725", "002384", "688072"]

def norm_key(d):
    return str(d).replace("-", "")

idx_raw = json.load(open(os.path.join(DATA, "index_5min_dh.json"), encoding="utf-8"))
idx5 = {norm_key(k): v for k, v in idx_raw.items()}

stock_bars = {}
for code in STOCKS:
    m5 = json.load(open(os.path.join(DATA, "stock_5min_%s.json" % code), encoding="utf-8"))
    stock_bars[code] = {norm_key(k): v for k, v in m5.items()}

def agg_daily(bars):
    if not bars:
        return None
    bars = sorted(bars, key=lambda b: str(b.get("trade_time") or b.get("time")))
    return {"open": float(bars[0]["open"]), "close": float(bars[-1]["close"]),
            "high": max(float(b["high"]) for b in bars),
            "low": min(float(b["low"]) for b in bars),
            "vol": sum(float(b.get("vol") or 0) for b in bars)}

def daily_series(bars_map):
    d = {}
    for ds, bars in bars_map.items():
        a = agg_daily(bars)
        if a:
            d[ds] = a
    return d

stock_daily = {c: daily_series(stock_bars[c]) for c in STOCKS}
idx_daily = daily_series(idx5)

all_days = sorted(set(idx_daily.keys()) & set(stock_daily[STOCKS[0]].keys()))
print("交易日:", len(all_days), all_days[0], "~", all_days[-1])

def fwd_close(daily, dt, n):
    keys = sorted(daily.keys())
    if dt not in keys:
        return None
    i = keys.index(dt)
    if i + n >= len(keys):
        return None
    return float(daily[keys[i + n]]["close"])

def sig_249(day_bars):
    bars = sorted(day_bars, key=lambda b: str(b.get("trade_time") or b.get("time")))
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    if len(closes) < 10:
        return False
    dh = highs[0]; mdd = 0.0
    for i in range(1, len(closes)):
        dh = max(dh, highs[i])
        dd = (dh - closes[i]) / dh * 100 if dh > 0 else 0
        mdd = max(mdd, dd)
    return 2.0 <= mdd < 3.0

def sig_A(prev_daily, cur_daily, tol=0.005):
    if not prev_daily or not cur_daily:
        return False
    return float(cur_daily["low"]) <= float(prev_daily["low"]) * (1 + tol)

def sig_B(day_bars, lo=2.0, hi=3.0):
    bars = sorted(day_bars, key=lambda b: str(b.get("trade_time") or b.get("time")))
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    if len(closes) < 10:
        return False
    dh = highs[0]; mdd = 0.0
    for i in range(1, len(closes)):
        dh = max(dh, highs[i])
        dd = (dh - closes[i]) / dh * 100 if dh > 0 else 0
        mdd = max(mdd, dd)
    return lo <= mdd < hi

def sig_C(day_bars, thresh=0.8):
    bars = sorted(day_bars, key=lambda b: str(b.get("trade_time") or b.get("time")))
    prev = None
    for b in bars:
        c = float(b["close"])
        if prev and prev > 0:
            if (c - prev) / prev * 100 <= -thresh:
                return True
        prev = c
    return False

def evaluate(get_signals, label):
    res = {"1": [], "2": []}
    sig_days_set = set()
    for code in STOCKS:
        daily = stock_daily[code]
        dk = sorted(daily.keys())
        for dt in dk:
            if dt not in idx_daily:
                continue
            try:
                ok = get_signals(dt, code)
            except Exception:
                ok = False
            if not ok:
                continue
            sig_days_set.add(dt)
            buy_px = float(daily[dt]["close"])
            for n in (1, 2):
                r = fwd_close(daily, dt, n)
                if r:
                    res[str(n)].append((r / buy_px - 1) * 100)
    parts = []
    for k in ("1", "2"):
        v = res[k]
        if v:
            parts.append("T+%s n=%d mean=%+.2f%% hit=%.2f" % (k, len(v), np.mean(v), np.mean(np.array(v) > 0)))
    print("%-40s 信号日=%3d 标的-日=%3d | %s" % (label, len(sig_days_set), len(res["1"]), " | ".join(parts) or "-"))

evaluate(lambda dt, c: dt in stock_daily[c], "基线(全交易日)")

evaluate(lambda dt, c: sig_249(idx5.get(dt) or []), "A0 现249 上证整日回撤2-3%")

def A_sig(dt, code):
    daily = stock_daily[code]
    keys = sorted(daily.keys())
    if dt not in keys:
        return False
    i = keys.index(dt)
    if i == 0:
        return False
    return sig_A(daily[keys[i - 1]], daily[dt])
evaluate(A_sig, "A 个股触及前日低点(tol0.5%)")

def A_sig_strict(dt, code):
    daily = stock_daily[code]
    keys = sorted(daily.keys())
    if dt not in keys:
        return False
    i = keys.index(dt)
    if i == 0:
        return False
    return sig_A(daily[keys[i - 1]], daily[dt], tol=0.0)
evaluate(A_sig_strict, "A' 个股跌破前日低点(严格)")

def A_sig_shrink(dt, code):
    daily = stock_daily[code]
    keys = sorted(daily.keys())
    if dt not in keys:
        return False
    i = keys.index(dt)
    if i == 0:
        return False
    cur = daily[dt]; prev = daily[keys[i - 1]]
    if not sig_A(prev, cur):
        return False
    return float(cur["vol"]) <= float(prev["vol"]) * 1.0
evaluate(A_sig_shrink, "A 触及前日低点+量≤前日")

def B_sig(dt, code):
    return sig_B(stock_bars[code].get(dt) or [])
evaluate(B_sig, "B 个股当日回撤2-3%")

def B_sig_low(dt, code):
    return sig_B(stock_bars[code].get(dt) or [], lo=1.5, hi=3.0)
evaluate(B_sig_low, "B' 个股当日回撤1.5-3%")

evaluate(lambda dt, c: sig_C(idx5.get(dt) or [], thresh=0.8), "C 大盘单根5min急杀>=0.8%")
evaluate(lambda dt, c: sig_C(idx5.get(dt) or [], thresh=1.0), "C' 大盘单根5min急杀>=1.0%")

# ── 补充: C 小阈值 + 组合 ──
evaluate(lambda dt, c: sig_C(idx5.get(dt) or [], thresh=0.3), "C0.3 大盘单根5min急杀>=0.3%")
evaluate(lambda dt, c: sig_C(idx5.get(dt) or [], thresh=0.4), "C0.4 大盘单根5min急杀>=0.4%")
evaluate(lambda dt, c: sig_C(idx5.get(dt) or [], thresh=0.5), "C0.5 大盘单根5min急杀>=0.5%")

# D: 大盘当日有分时急杀(>=0.4%) 且 个股缩量触及/跌破前日低点
# 狼大语义: 大盘带下来 + 缩量才有低点 (2025-09-02)
def D_sig(dt, code):
    if not sig_C(idx5.get(dt) or [], thresh=0.4):
        return False
    daily = stock_daily[code]
    keys = sorted(daily.keys())
    if dt not in keys:
        return False
    i = keys.index(dt)
    if i == 0:
        return False
    prev = daily[keys[i - 1]]; cur = daily[dt]
    return sig_A(prev, cur) and float(cur["vol"]) <= float(prev["vol"]) * 1.0
evaluate(D_sig, "D 大盘急杀0.4%+个股缩量触前低")
