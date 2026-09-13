# -*- coding: utf-8 -*-
import json, os
import numpy as np
DATA = os.environ.get("DATA_DIR","data")
SYMS = ["000977","002165","002371","002945","159516","300308","301018","588170","601138","603259","603757","688981"]
def load(code):
    try: return json.load(open(os.path.join(DATA,"recent_sync",code+".json"),encoding="utf-8"))
    except Exception: return {}
def byday(d,day): return sorted(d.get(day,[]), key=lambda b: str(b.get("time") or b.get("trade_time")))
def find_buy(bars, prev_low, prev_vol_avg):
    hi = float(bars[0]["high"]); ca = 0.0; cv = 0.0
    for i,b in enumerate(bars):
        lo=float(b["low"]); cl=float(b["close"]); vol=float(b.get("vol") or 0)
        hi=max(hi, float(b["high"]))
        cv += vol; ca += float(b.get("amount") or 0)
        vwap = ca/cv if cv>0 else 0
        if prev_low and lo <= prev_low*1.005 and prev_vol_avg>0 and vol <= prev_vol_avg*0.7 and cl>vwap:
            return ("254", lo, i)
        if hi>0 and (lo/hi-1)*100 <= -2.5:
            return ("zT", lo, i)
    return None
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
            return {"idx":idx,"price":closes[idx]}
    except Exception: return None
    return None
def run(code):
    d=load(code); days=sorted(d.keys()); r={"confirm":[], "dayend":[], "nbuy":0}
    prev_low=None; prev_vol_avg=0.0
    for di,day in enumerate(days):
        bars=byday(d,day)
        if len(bars)<20: continue
        if di>0:
            pb=byday(d, days[di-1])
            if pb: prev_low=min(float(b["low"]) for b in pb); prev_vol_avg=np.mean([float(b.get("vol") or 0) for b in pb])
        if prev_low is None or prev_vol_avg<=0: continue
        buy=find_buy(bars, prev_low, prev_vol_avg)
        if not buy: continue
        kind, bp, bi = buy; entry=bp; post=bars[bi+1:]
        if len(post)<6: continue
        v1=t_sell_v1(post); close=float(bars[-1]["close"])
        def pnl(p): return (p/entry-1)*100
        if v1: r["confirm"].append((day, kind, round(pnl(v1["price"]),2)))
        else: r["dayend"].append((day, kind, round(pnl(close),2)))
        r["nbuy"]+=1
    return r
def stat(v):
    if not v: return "n=0"
    p=[x[2] for x in v]; return "n=%d avg=%+.2f%% win=%d%% sum=%+.2f%%" % (len(v), np.mean(p), np.mean(np.array(p)>0)*100, np.sum(p))
allc=[]; alld=[]; allk={}
print("code | confirm_T出 | day_end | nbuy")
for code in SYMS:
    r=run(code); allc+=r["confirm"]; alld+=r["dayend"]
    for x in r["confirm"]+r["dayend"]: allk[x[1]]=allk.get(x[1],0)+1
    print("%s | %s | %s | %d" % (code, stat(r["confirm"]), stat(r["dayend"]), r["nbuy"]))
print("== 汇总(真实买点 254/正T低吸) ==")
print("confirm_T出:", stat(allc))
print("day_end:", stat(alld))
print("买点分布:", allk)
import collections
mc=collections.defaultdict(list); md=collections.defaultdict(list)
for x in allc: mc[x[0]].append(x[2])
for x in alld: md[x[0]].append(x[2])
common=[d for d in mc if d in md]
if common:
    a=np.array([np.mean(mc[d]) for d in common]); b=np.array([np.mean(md[d]) for d in common])
    print("== 同日比较 n=%d ==" % len(common))
    print("确认T出 avg=%+.2f%% win=%d%% | day_end avg=%+.2f%% win=%d%%" % (np.mean(a), np.mean(a>0)*100, np.mean(b), np.mean(b>0)*100))
