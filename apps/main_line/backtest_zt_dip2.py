# -*- coding: utf-8 -*-
"""正T 大盘跳水：分半验证 + 阈值延伸 + 分布检查"""
import json, os
import pandas as pd, numpy as np

DATA = os.environ.get("DATA_DIR", "data")
idx5_raw = json.load(open(os.path.join(DATA, "index_5min_dh.json"), encoding="utf-8"))
idx5 = {k.replace("-", ""): v for k, v in idx5_raw.items()}

def agg_daily(bars):
    if not bars: return None
    bars = sorted(bars, key=lambda b: str(b.get("trade_time") or b.get("time")))
    return {"open": float(bars[0]["open"]), "close": float(bars[-1]["close"]),
            "high": max(float(b["high"]) for b in bars), "low": min(float(b["low"]) for b in bars)}

def idx_max_dd(bars):
    """指数当日最大回撤% (从日高到任一收盘)"""
    bars = sorted(bars, key=lambda b: str(b.get("trade_time") or b.get("time")))
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    if len(closes) < 10: return 0.0
    dh = highs[0]; mdd = 0.0
    for i in range(1, len(closes)):
        dh = max(dh, highs[i])
        mdd = max(mdd, (dh - closes[i]) / dh * 100 if dh > 0 else 0)
    return mdd

stocks = ["603259", "603678"]
stock_daily = {}
for code in stocks:
    m5 = json.load(open(os.path.join(DATA, "stock_5min_%s.json" % code), encoding="utf-8"))
    d = {}
    for ds, bars in m5.items():
        agg = agg_daily(bars)
        if agg: d[ds] = agg
    stock_daily[code] = d

all_days = sorted(stock_daily[stocks[0]].keys())
half = len(all_days) // 2

# 每日指数最大回撤（缓存）
idx_mdd = {dt: idx_max_dd(idx5[dt]) for dt in all_days if dt in idx5}

def collect(th, seg=None):
    g = {"1": [], "2": [], "3": []}; days_hit = set()
    for code in stocks:
        daily = stock_daily[code]
        for dt in all_days:
            if seg == "h1" and all_days.index(dt) >= half: continue
            if seg == "h2" and all_days.index(dt) < half: continue
            if dt not in daily: continue
            mdd = idx_mdd.get(dt, 0)
            if mdd < th: continue
            days_hit.add(dt)
            for n in (1, 2, 3):
                keys = sorted(daily.keys())
                if dt not in keys: continue
                i = keys.index(dt)
                if i + n >= len(keys): continue
                r = float(daily[keys[i+n]]["close"]) / float(daily[dt]["close"]) - 1
                g[str(n)].append(r * 100)
    return g, days_hit

print("== 分半验证 + 阈值延伸（指数日最大回撤%）==")
print(f"{'阈值':<8}{'seg':<5}{'信号日':<6}{'T+1':<22}{'T+2':<22}{'T+3':<22}")
for th in (1.0, 1.5, 2.0, 2.5, 3.0):
    for seg in ("all", "h1", "h2"):
        g, dh = collect(th, seg if seg != "all" else None)
        if not g["1"]:
            print(f"{th:<8}{seg:<5}{len(dh):<6} n=0"); continue
        parts = []
        for k in ("1", "2", "3"):
            v = g[k]
            if v: parts.append(f"n{len(v)} {np.mean(v):+.2f}% h{np.mean(np.array(v)>0):.2f}")
        print(f"{th:<8}{seg:<5}{len(dh):<6}" + " | ".join(parts))
    print()

# A_dd2.0 分布细节
g, dh = collect(2.0)
v = np.array(g["1"])
print("== dd>=2.0 信号日 T+1 分布 ==")
print("n=%d mean=%.2f%% med=%.2f%% hit=%.2f min=%.2f%% max=%.2f%%" % (len(v), v.mean(), np.median(v), (v>0).mean(), v.min(), v.max()))
print("信号日:", sorted(dh))
print("个股分布:", {c: sum(1 for x in range(len(all_days)) if all_days[x] in dh and all_days[x] in stock_daily[c]) for c in stocks})

json.dump({"idx_mdd_days": {k: round(v,2) for k, v in idx_mdd.items()}}, open(os.path.join(DATA, "zt_dip_detail.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("WROTE data/zt_dip_detail.json")