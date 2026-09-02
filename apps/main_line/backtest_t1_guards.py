# -*- coding: utf-8 -*-
"""T1缩转放 护栏复测 v3（5min指数, 盘中逐bar, 排除开盘/午休效应）
变体:
  V0  基线(生产 1.2x 全部时点)
  V5  排除 09:30/09:35/13:05 (午休/开盘伪信号)
  V6  V5 + 放量>1.5x
  V7  V5 + 回调护栏(触发价距日高回撤>=1%)
  V8  V5 + 跌势护栏(触发<前收)
"""
import json, os
import pandas as pd, numpy as np
from collections import Counter

DATA = os.environ.get("DATA_DIR", "data")
m5_raw = json.load(open(os.path.join(DATA, "index_5min_dh.json"), encoding="utf-8"))
daily_raw = json.load(open(os.path.join(DATA, "index_daily_000001.json"), encoding="utf-8"))
df = pd.DataFrame(daily_raw); df["trade_date"] = pd.to_datetime(df["trade_date"])
df = df.sort_values("trade_date").reset_index(drop=True)
ser = df.set_index("trade_date")["close"].astype(float)

EXCLUDE = ("09:30", "09:35", "13:05")

def t1_at(vols, mult=1.2):
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

groups = {k: {"5": [], "3": [], "1": []} for k in ("v0","v5","v6","v7","v8","none")}
detail = {k: [] for k in ("v0","v5","v6","v7","v8")}
time_dist = Counter()
for ds, bars in sorted(m5_raw.items()):
    bars = sorted(bars, key=lambda b: str(b.get("trade_time") or b.get("time")))
    dt = pd.Timestamp(ds)
    if dt not in ser.index: continue
    i0 = df.index[df["trade_date"] == dt][0]
    closes = np.array([float(b["close"]) for b in bars])
    highs = np.array([float(b["high"]) for b in bars])
    lows = np.array([float(b["low"]) for b in bars])
    vols = np.array([float(b["vol"]) for b in bars])
    times = [str(b.get("trade_time") or b.get("time"))[11:16] for b in bars]
    prev_close = float(ser.iloc[i0-1]) if i0 >= 1 else None

    def scan(mult, skip_effect):
        for i in range(17, len(bars)):
            if skip_effect and times[i] in EXCLUDE: continue
            if t1_at(vols[:i+1], mult):
                return i
        return None

    hi_v0 = scan(1.2, False)
    hi_v5 = scan(1.2, True)
    hi_v6 = scan(1.5, True)
    if hi_v0 is None:
        for n in ("5","3","1"):
            r = fwd(ser, dt, int(n))
            if r is not None: groups["none"][n].append(r)
    else:
        time_dist[times[hi_v0]] += 1
    for key, hi in (("v0", hi_v0), ("v5", hi_v5), ("v6", hi_v6)):
        if hi is None: continue
        tc = float(closes[hi]); dh = float(highs.max()); dl = float(lows.min())
        v7 = bool(dh > 0 and (dh - tc) / dh * 100 >= 1.0)
        v8 = bool(prev_close is not None and tc < prev_close)
        for n in ("5","3","1"):
            r = fwd(ser, dt, int(n))
            if r is None: continue
            groups[key][n].append(r)
            if key == "v5":
                if v7: groups["v7"][n].append(r)
                if v8: groups["v8"][n].append(r)
        detail[key].append((ds, times[hi], round(tc,2), round(dh,2), round(dl,2), v7, v8))

def show(name, g):
    if not g.get("5"): print(f"  {name}: n=0"); return
    parts = []
    for k in ("1","3","5"):
        v = g[k]
        if v: parts.append(f"{k}日 n={len(v)} mean={np.mean(v):+.2f}% hit={np.mean(np.array(v)>0):.2f}")
    print(f"  {name} ({len(g['5'])}次): " + " | ".join(parts))

print(f"== T1缩转放 v3 (5min指数, {len(m5_raw)}天) ==")
show("V0 基线(1.2x全部)", groups["v0"])
show("V5 排除效应(1.2x)", groups["v5"])
show("V6 排除+1.5x", groups["v6"])
show("V7 排除+回调(日高-1%)", groups["v7"])
show("V8 排除+跌势(<前收)", groups["v8"])
show("无信号", groups["none"])
print()
print("触发次数: V0=%d V5=%d V6=%d V7=%d V8=%d" % tuple(len(detail[k]) for k in ("v0","v5","v6","v7","v8")))
print("V0 时点分布 top:", time_dist.most_common(8))
json.dump({"groups": groups, "detail": detail, "time_dist": dict(time_dist)},
          open(os.path.join(DATA, "t1_guards_backtest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("WROTE data/t1_guards_backtest.json")
