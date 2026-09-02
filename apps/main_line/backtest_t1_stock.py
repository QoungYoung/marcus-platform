# -*- coding: utf-8 -*-
"""个股 T1缩转放 回测（5min, brze 拉取 2025-12~2026-09）
用生产 _t_signals_from_m5（已含排除 09:30/09:35/13:05 结构效应）逐bar扫描
前向收益: 从当日收盘算 1/3/5 日；对照无信号组
数据: data/stock_5min_{603259,603678}.json + index_daily_000001.json(交易日历)
"""
import json, os, sys
import pandas as pd, numpy as np

DATA = os.environ.get("DATA_DIR", "data")
daily_raw = json.load(open(os.path.join(DATA, "index_daily_000001.json"), encoding="utf-8"))
df = pd.DataFrame(daily_raw); df["trade_date"] = pd.to_datetime(df["trade_date"])
df = df.sort_values("trade_date").reset_index(drop=True)
ser = df.set_index("trade_date")["close"].astype(float)

sys.path.insert(0, "/home/fengx/marcus-platform/backend")
from app.services.t_monitor import _t_signals_from_m5

def fwd(s, upto, n):
    base = s[s.index <= upto]; fut = s[s.index > upto]
    if len(base) < 1 or len(fut) < n: return None
    b0 = float(base.iloc[-1])
    return (float(fut.iloc[n-1]) / b0 - 1) * 100 if b0 > 0 else None

for code in ("603259", "603678"):
    path = os.path.join(DATA, "stock_5min_%s.json" % code)
    if not os.path.exists(path):
        print(code, "NO DATA"); continue
    m5 = json.load(open(path, encoding="utf-8"))
    g_t = {"1": [], "3": [], "5": []}; g_n = {"1": [], "3": [], "5": []}
    n_trig = 0; times = []
    for ds, bars in sorted(m5.items()):
        if not bars: continue
        bars = sorted(bars, key=lambda b: str(b.get("trade_time") or b.get("time")))
        dt = pd.Timestamp(ds)
        if dt not in ser.index: continue
        hit = None
        for i in range(17, len(bars)):
            t1, _ = _t_signals_from_m5(bars[:i+1])
            if t1:
                hit = i; break
        if hit is None:
            for n in ("1","3","5"):
                r = fwd(ser, dt, int(n))
                if r is not None: g_n[n].append(r)
            continue
        n_trig += 1
        times.append(str(bars[hit].get("trade_time") or bars[hit].get("time"))[11:16])
        for n in ("1","3","5"):
            r = fwd(ser, dt, int(n))
            if r is not None: g_t[n].append(r)
    def show(name, g):
        if not g["5"]: print(f"  {name}: n=0"); return
        parts = []
        for k in ("1","3","5"):
            v = g[k]
            if v: parts.append(f"{k}日 n={len(v)} mean={np.mean(v):+.2f}% hit={np.mean(np.array(v)>0):.2f}")
        print(f"  {name} ({len(g['5'])}次): " + " | ".join(parts))
    print(f"== 个股 {code} T1 (排除效应, {len([d for d in m5.values() if d])}天有数据) ==")
    show("T1触发", g_t)
    show("无信号", g_n)
    from collections import Counter
    print("  触发时点 top8:", Counter(times).most_common(8))
