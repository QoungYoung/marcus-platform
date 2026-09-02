# -*- coding: utf-8 -*-
"""T1 v3 补充: 1.4x 变体 + 1.5x 分半稳健性（防过拟合）"""
import json, os
import pandas as pd, numpy as np

DATA = os.environ.get("DATA_DIR", "data")
m5_raw = json.load(open(os.path.join(DATA, "index_5min_dh.json"), encoding="utf-8"))
daily_raw = json.load(open(os.path.join(DATA, "index_daily_000001.json"), encoding="utf-8"))
df = pd.DataFrame(daily_raw); df["trade_date"] = pd.to_datetime(df["trade_date"])
df = df.sort_values("trade_date").reset_index(drop=True)
ser = df.set_index("trade_date")["close"].astype(float)
EXCLUDE = ("09:30", "09:35", "13:05")

def t1_at(vols, mult):
    n = len(vols)
    if n < 18: return False
    prev = vols[-9:-1]
    if prev.mean() <= 0: return False
    return bool(prev[-1] <= prev[0] and vols[-1] > prev.mean() * mult)

def fwd(s, upto, n):
    base = s[s.index <= upto]; fut = s[s.index > upto]
    if len(base) < 1 or len(fut) < n: return None
    b0 = float(base.iloc[-1])
    return (float(fut.iloc[n-1]) / b0 - 1) * 100 if b0 > 0 else None

days = sorted(m5_raw.keys())
half = len(days) // 2
results = {m: {"all": {"5":[],"3":[],"1":[]}, "h1": {"5":[],"3":[],"1":[]}, "h2": {"5":[],"3":[],"1":[]}} for m in (1.3, 1.4, 1.5)}
for ds in days:
    bars = sorted(m5_raw[ds], key=lambda b: str(b.get("trade_time") or b.get("time")))
    dt = pd.Timestamp(ds)
    if dt not in ser.index: continue
    i0 = df.index[df["trade_date"] == dt][0]
    vols = np.array([float(b["vol"]) for b in bars])
    times = [str(b.get("trade_time") or b.get("time"))[11:16] for b in bars]
    seg = "h1" if days.index(ds) < half else "h2"
    for mult in (1.3, 1.4, 1.5):
        hit = None
        for i in range(17, len(bars)):
            if times[i] in EXCLUDE: continue
            if t1_at(vols[:i+1], mult):
                hit = i; break
        if hit is None: continue
        for n in ("5","3","1"):
            r = fwd(ser, dt, int(n))
            if r is None: continue
            results[mult]["all"][n].append(r)
            results[mult][seg][n].append(r)

for mult in (1.3, 1.4, 1.5):
    print(f"== mult={mult} ==")
    for seg in ("all","h1","h2"):
        g = results[mult][seg]
        if not g["5"]: print(f"  {seg}: n=0"); continue
        parts = []
        for k in ("1","3","5"):
            v = g[k]
            if v: parts.append(f"{k}日 n={len(v)} mean={np.mean(v):+.2f}% hit={np.mean(np.array(v)>0):.2f}")
        print(f"  {seg} ({len(g['5'])}次): " + " | ".join(parts))
json.dump(results, open(os.path.join(DATA, "t1_guards_sensitivity.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("WROTE data/t1_guards_sensitivity.json")
