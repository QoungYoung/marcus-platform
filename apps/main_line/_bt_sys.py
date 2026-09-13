# -*- coding: utf-8 -*-
import json, sys
sys.path.insert(0,"/app/apps/main_line")
import pandas as pd, numpy as np
import position_class as pc
hist=json.load(open("/app/data/concept_hist.json",encoding="utf-8"))
def ser(code): idx=pd.to_datetime(hist[code]["dates"]); return pd.Series(hist[code]["close"],index=idx)
def fwd(s,upto,n):
    base=s[s.index<=pd.Timestamp(upto)]; fut=s[s.index>pd.Timestamp(upto)]
    if len(base)<1 or len(fut)<n or base.isna().any() or fut.iloc[:n].isna().any(): return None
    return (float(fut.iloc[n-1])/float(base.iloc[-1])-1)*100
def fund(code,upto):
    idx=pd.to_datetime(hist[code]["dates"]); s=pd.Series(hist[code]["net_amount"],index=idx)[lambda x:x.index<=pd.Timestamp(upto)]
    last=s.iloc[-1] if len(s) else 0
    return "in" if last>0 else ("out" if last<0 else "flat")
dates=["2025-12-01","2026-01-05","2026-02-02","2026-03-02","2026-04-01","2026-05-06","2026-06-01"]
# 分组: 位置 与 位置+资金确认
agg={"pos_HIGH":[],"pos_LOW":[],"HIGH_out":[],"HIGH_in":[],"LOW_in":[]}
for dt in dates:
    for code in hist:
        s=ser(code); base=s[s.index<=pd.Timestamp(dt)]
        if len(base)<60 or float(base.min())<=0 or base.isna().any(): continue
        f=pc.position_features(base)
        if f is None: continue
        pos=pc.classify(f)["position"]; fd=fund(code,dt)
        for n in [20,60]:
            rf=fwd(s,dt,n)
            if rf is None: continue
            key=f"pos_{pos}"
            agg.setdefault(key,[]).append((dt,rf))
            if pos=="HIGH": agg.setdefault("HIGH_"+fd,[]).append((dt,rf))
            if pos=="LOW": agg.setdefault("LOW_"+fd,[]).append((dt,rf))
print("=== 位置分组 平均前向(20/60日) ===")
for k in ["pos_HIGH","pos_MID","pos_LOW"]:
    v=[r for d,r in agg.get(k,[])]
    if v: print(f"  {k:9} n={len(v):3} 20d={np.mean([r for d,r in agg.get(k,[]) if d and False] or []) if False else '': }")
# better: separate by horizon
def mean_by_horizon(key, periods=(20,60)):
    out={}
    for n in periods:
        v=[r for d,r in agg[key] if (pd.Timestamp(d)+pd.Timedelta(days=0), r) and True]
    return None
# simpler aggregate per position over both horizons pooled + per-horizon
print("=== 位置 20日/60日 ===")
for k in ["pos_HIGH","pos_MID","pos_LOW"]:
    for n in [20,60]:
        v=[r for (d,r) in agg[k] if (pd.Timestamp(d)+pd.Timedelta(days=n-n)) and False]  # placeholder
    for n in [20,60]:
        v=[r for (d,r) in agg[k]]
        if v: pass
# direct: collect per (key,n)
data={}
for dt in dates:
    for code in hist:
        s=ser(code); base=s[s.index<=pd.Timestamp(dt)]
        if len(base)<60 or float(base.min())<=0 or base.isna().any(): continue
        f=pc.position_features(base)
        if f is None: continue
        pos=pc.classify(f)["position"]; fd=fund(code,dt)
        for n in [20,60]:
            rf=fwd(s,dt,n)
            if rf is None: continue
            data.setdefault(pos,{}).setdefault(n,[]).append(rf)
            if pos=="HIGH": data.setdefault("HIGH_"+fd,{}).setdefault(n,[]).append(rf)
for k in ["HIGH","MID","LOW","HIGH_out","HIGH_in"]:
    for n in [20,60]:
        v=data.get(k,{}).get(n,[])
        if v: print(f"  {k:8} {n}日 n={len(v):3} avg={np.mean(v):6.2f}% 中位={np.median(v):6.2f}%")
