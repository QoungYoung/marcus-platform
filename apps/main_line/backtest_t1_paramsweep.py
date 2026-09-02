# -*- coding: utf-8 -*-
"""个股 T1 参数扫描：mult x 连续缩量根数 → 触发率 + 前向收益"""
import json, os, sys
import pandas as pd, numpy as np
from collections import Counter

DATA = "/home/fengx/marcus-platform/data"
daily_raw = json.load(open(os.path.join(DATA, "index_daily_000001.json"), encoding="utf-8"))
df = pd.DataFrame(daily_raw); df["trade_date"] = pd.to_datetime(df["trade_date"])
df = df.sort_values("trade_date").reset_index(drop=True)
ser = df.set_index("trade_date")["close"].astype(float)

def t1_variant(vols, mult, min_shrink):
    """缩量要求: 最近 min_shrink 根单调不增(连续缩量) + 末端放量>mult倍"""
    n = len(vols)
    if n < 18: return False
    tail = vols[-min_shrink-1:-1]
    if len(tail) < 2: return False
    mono = all(tail[i+1] <= tail[i] for i in range(len(tail)-1))
    base = vols[-9:-1]
    if base.mean() <= 0: return False
    return bool(mono and vols[-1] > base.mean() * mult)

EXCLUDE = ("0930", "0935", "1305")
def hhmm(b):
    t = str(b.get("trade_time") or b.get("time") or "")
    t = t.strip()
    if len(t) >= 12 and t.isdigit(): return t[8:12]
    for sep in (" ", "T"):
        if sep in t: t = t.split(sep)[1]
    parts = t.split(":")
    return (parts[0] + parts[1]) if len(parts) >= 2 else t

def fwd(s, upto, n):
    base = s[s.index <= upto]; fut = s[s.index > upto]
    if len(base) < 1 or len(fut) < n: return None
    b0 = float(base.iloc[-1])
    return (float(fut.iloc[n-1]) / b0 - 1) * 100 if b0 > 0 else None

print(f"{'code':<8}{'mult':<6}{'shrink':<8}{'trig':<7}{'1d_mean':<9}{'1d_hit':<8}{'3d_mean':<9}{'3d_hit':<8}{'5d_mean':<9}{'5d_hit':<8}")
for code in ("603259", "603678"):
    m5 = json.load(open(os.path.join(DATA, "stock_5min_%s.json" % code), encoding="utf-8"))
    for mult in (1.2, 1.5, 2.0):
        for shrink in (2, 4, 6):
            g = {"1": [], "3": [], "5": []}; ntrig = 0
            for ds, bars in sorted(m5.items()):
                if not bars: continue
                bars = sorted(bars, key=lambda b: str(b.get("trade_time") or b.get("time")))
                dt = pd.Timestamp(ds)
                if dt not in ser.index: continue
                vols = np.array([float(b["vol"]) for b in bars])
                times = [hhmm(b) for b in bars]
                hit = False
                for i in range(17, len(bars)):
                    if times[i] in EXCLUDE: continue
                    if t1_variant(vols[:i+1], mult, shrink):
                        hit = True; break
                if not hit: continue
                ntrig += 1
                for n in ("1","3","5"):
                    r = fwd(ser, dt, int(n))
                    if r is not None: g[n].append(r)
            if not g["5"]:
                print(f"{code:<8}{mult:<6}{shrink:<8}0       -          -          -          -          -          -")
                continue
            p = []
            for k in ("1","3","5"):
                v = g[k]
                p.append(f"{np.mean(v):+.2f}%")
                p.append(f"{np.mean(np.array(v)>0):.2f}")
            print(f"{code:<8}{mult:<6}{shrink:<8}{ntrig:<7}{p[0]:<9}{p[1]:<8}{p[2]:<9}{p[3]:<8}{p[4]:<9}{p[5]:<8}")
