# -*- coding: utf-8 -*-
"""资金流入TOP概念的高低位分类验证 + 对狼大一致性评估。"""
import json, sys
sys.path.insert(0,"/app")
import pandas as pd, numpy as np
HIST="/app/data/concept_hist.json"
hist=json.load(open(HIST,encoding="utf-8"))
def ser_for(code, upto):
    a=hist[code]; idx=pd.to_datetime(a["dates"]); s=pd.Series(a["close"],index=idx)
    return s[s.index<=pd.Timestamp(upto)].dropna()
def pos_feats(ser):
    if len(ser)<30: return None
    c=float(ser.iloc[-1]); hi=float(ser.iloc[-250:].max()); lo=float(ser.iloc[-250:].min())
    box=ser.iloc[-30:]; bhi,blo=float(box.max()),float(box.min()); rng=bhi-blo or 1
    ma60=float(ser.iloc[-60:].mean()) if len(ser)>=60 else float(ser.mean())
    ma200=float(ser.iloc[-200:].mean()) if len(ser)>=200 else float(ser.iloc[-120:].mean())
    m60a=float(ser.iloc[-60:].mean()) if len(ser)>=60 else float(ser.mean())
    m60b=float(ser.iloc[-30:].mean()) if len(ser)>=30 else float(ser.mean())
    slope=(m60a-m60b)/abs(m60b or 1)
    return {"close":round(c,1),"vh":round((c/hi-1)*100,1),"box":round((c-blo)/rng*100,1),
            "vma60":round((c/ma60-1)*100,1),"slope":round(slope*100,3)}
def classify(f):
    if not f: return "UNKNOWN"
    if f["vh"]<=-30 and f["box"]<=35 and f["vma60"]<0: return "LOW"
    if f["vh"]>=-10 and f["box"]>=65 and f["vma60"]>0: return "HIGH"
    return "MID"
def net_at(code, upto):
    a=hist[code]; idx=pd.to_datetime(a["dates"]); s=pd.Series(a["net_amount"],index=idx)
    s=s[s.index<=pd.Timestamp(upto)]
    return round(float(s.iloc[-1])/1e8,1) if len(s) else None
for label,upto in [("当前 2026-08-31(高位)","2026-08-31"),("回踩 2026-07-17(低点)","2026-07-17")]:
    print("="*66); print("EVAL DATE",label, upto)
    rows=[]
    for code,a in hist.items():
        na=net_at(code,upto)
        if na is None: continue
        f=pos_feats(ser_for(code,upto))
        rows.append((a["name"], classify(f), na, f))
    rows.sort(key=lambda x:-(x[2] or 0))
    top=rows[:20]
    print("--- 资金流入TOP 20 ---")
    for nm,pos,na,f in top:
        if f: print(f"{nm:10} {pos:5} 净流入={na:8}亿  距高={f['vh']:6}%  箱体={f['box']:4}%  MA60={f['vma60']:6}%")
