# -*- coding: utf-8 -*-
"""tsell_v1_v2_backtest.py — 对比卖出规则：V1=当前确认制T出 vs V2=加半小时窗+量价背离。统一买点=当日最低点(隔绝买入端)。"""
import json, os
import numpy as np
DATA = os.environ.get("DATA_DIR","data")
SYMS = ["000977","002165","002371","002945","159516","300308","301018","588170","601138","603259","603757","688981"]
def load(code):
    try: return json.load(open(os.path.join(DATA,"recent_sync",code+".json"),encoding="utf-8"))
    except Exception: return {}
def byday(d,day): return sorted(d.get(day,[]), key=lambda b: str(b.get("time") or b.get("trade_time")))
def t_sell_v1(bars, look=8):
    try:
        closes=np.array([float(b["close"]) for b in bars]); vols=np.array([float(b.get("vol") or 0) for b in bars]); n=len(closes)
        if n < look*2+4: return None
        start=None
        for i in range(1, n-look-2):
            base=vols[max(0,i-look):i].mean()
            if base>0 and vols[i]>base*1.3: start=i; break
        if start is None: return None
        seg=closes[start:n]; hi=float(seg.max()); hi_idx=start+int(seg.argmax())
        if hi_idx < start+1: return None
        va=vols[hi_idx+1:hi_idx+1+look].mean() if hi_idx+look<n else 0; vb=vols[max(start,hi_idx-look):hi_idx].mean()
        if not (vb>0 and va<vb*0.8): return None
        after=closes[hi_idx+1:]; sh=float(after.max()) if len(after) else 0
        if sh > hi*0.98 and sh < hi*1.005:
            idx=hi_idx+1+int(np.argmax(after)) if len(after) else hi_idx+1
            return {"idx":idx,"price":closes[idx],"hi":hi,"sh":sh}
    except Exception: return None
    return None
def t_sell_v2(bars, look=8, win=6):
    try:
        closes=np.array([float(b["close"]) for b in bars]); vols=np.array([float(b.get("vol") or 0) for b in bars]); n=len(closes)
        if n < look*2+4: return None
        start=None
        for i in range(1, n-look-2):
            base=vols[max(0,i-look):i].mean()
            if base>0 and vols[i]>base*1.3: start=i; break
        if start is None: return None
        seg=closes[start:n]; hi=float(seg.max()); hi_idx=start+int(seg.argmax())
        if hi_idx < start+1: return None
        va=vols[hi_idx+1:hi_idx+1+look].mean() if hi_idx+look<n else 0; vb=vols[max(start,hi_idx-look):hi_idx].mean()
        if not (vb>0 and va<vb*0.8): return None
        wend=min(hi_idx+win, n)
        if wend <= hi_idx+1: return None
        after=closes[hi_idx+1:wend]; a_vol=vols[hi_idx+1:wend]
        jm=int(np.argmax(after)) if len(after) else -1
        if jm<0: return None
        sh=float(after[jm])
        if sh < hi*0.98 or sh >= hi*1.005: return None
        if not (vols[hi_idx] > a_vol[jm]): return None
        cs=hi_idx+1+jm
        if cs+win >= n: return None
        lm=float(closes[cs:cs+win+1].max())
        if lm >= hi*1.005: return None
        idx=cs+int(np.argmax(closes[cs:cs+win+1])) if lm>sh else cs
        return {"idx":idx,"price":closes[idx],"hi":hi,"sh":sh}
    except Exception: return None
    return None
def run(code):
    d=load(code); days=sorted(d.keys()); r={"v1":[],"v2":[],"none":[]}
    for day in days:
        bars=byday(d,day)
        if len(bars)<20: continue
        lows=[float(b["low"]) for b in bars]; e=int(np.argmin(lows)); entry=lows[e]; post=bars[e+1:]
        if len(post)<6: continue
        v1=t_sell_v1(post); v2=t_sell_v2(post); close=float(bars[-1]["close"])
        def pnl(p): return (p/entry-1)*100
        if v1: r["v1"].append((day, round(pnl(v1["price"]),2)))
        if v2: r["v2"].append((day, round(pnl(v2["price"]),2)))
        if not v1 and not v2: r["none"].append((day, round(pnl(close),2)))
    return r
def stat(v):
    if not v: return "n=0"
    p=[x[1] for x in v]; return f"n={len(v)} avg={np.mean(p):+.2f}% hit={np.mean(np.array(p)>0)*100:.0f}% sum={np.sum(p):+.2f}%"
allv1=[]; allv2=[]; allnone=[]
print("code | V1确认制T出 | V2加30分窗+背离 | 均未触发→收盘")
for code in SYMS:
    r=run(code); allv1+=r["v1"]; allv2+=r["v2"]; allnone+=r["none"]
    print(f"{code} | {stat(r['v1'])} | {stat(r['v2'])} | n={len(r['none'])}")
print("\n== 汇总 ==")
print("V1 确认制T出:", stat(allv1))
print("V2 加30分窗+背离:", stat(allv2))
print("均未触发(收盘):", f"n={len(allnone)} avg={np.mean([x[1] for x in allnone]):+.2f}% hit={np.mean(np.array([x[1] for x in allnone])>0)*100:.0f}%" if allnone else "n=0")
m1=dict(allv1); m2=dict(allv2); common=[d for d in m1 if d in m2]
if common:
    a=np.array([m1[d] for d in common]); b=np.array([m2[d] for d in common])
    print(f"\n== 同日成对 (n={len(common)}) ==  V1 avg={np.mean(a):+.2f}% | V2 avg={np.mean(b):+.2f}% | V2-V1={np.mean(b-a):+.2f}% | V2优={np.mean(b>a)*100:.0f}%")
