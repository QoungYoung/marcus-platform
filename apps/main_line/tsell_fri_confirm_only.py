# -*- coding: utf-8 -*-
import json, os, datetime
import numpy as np
DATA = os.environ.get("DATA_DIR","data")
SYMS = ["000977","002165","002371","002945","159516","300308","301018","588170","601138","603259","603757","688981"]
def load(code):
    try: return json.load(open(os.path.join(DATA,"recent_sync",code+".json"),encoding="utf-8"))
    except Exception: return {}
def byday(d,day): return sorted(d.get(day,[]), key=lambda b: str(b.get("time") or b.get("trade_time")))
def is_fri(day):
    try: return datetime.datetime.strptime(str(day),"%Y%m%d").weekday()==4
    except Exception: return False
def find_buy(bars, prev_low, prev_vol_avg):
    hi=float(bars[0]["high"]); ca=0.0; cv=0.0
    for i,b in enumerate(bars):
        lo=float(b["low"]); cl=float(b["close"]); vol=float(b.get("vol") or 0)
        hi=max(hi,float(b["high"])); cv+=vol; ca+=float(b.get("amount") or 0)
        vwap=ca/cv if cv>0 else 0
        if prev_low and lo<=prev_low*1.005 and prev_vol_avg>0 and vol<=prev_vol_avg*0.7 and cl>vwap: return ("254",lo,i)
        if hi>0 and (lo/hi-1)*100<=-2.5: return ("zT",lo,i)
    return None
def t_sell_v1(bars, look=8):
    try:
        closes=np.array([float(b["close"]) for b in bars]); vols=np.array([float(b.get("vol") or 0) for b in bars]); n=len(closes)
        if n<look*2+4: return None
        start=None
        for i in range(1,n-look-2):
            base=vols[max(0,i-look):i].mean()
            if base>0 and vols[i]>base*1.3: start=i; break
        if start is None: return None
        seg=closes[start:n]; hi=float(seg.max()); hi_idx=start+int(seg.argmax())
        if hi_idx<start+1: return None
        va=vols[hi_idx+1:hi_idx+1+look].mean() if hi_idx+look<n else 0; vb=vols[max(start,hi_idx-look):hi_idx].mean()
        if not (vb>0 and va<vb*0.8): return None
        after=closes[hi_idx+1:]; sh=float(after.max()) if len(after) else 0
        if sh>hi*0.98 and sh<hi*1.005:
            idx=hi_idx+1+int(np.argmax(after)) if len(after) else hi_idx+1
            return {"idx":idx,"price":closes[idx]}
    except Exception: return None
    return None
friday=[]  # (day, kind, confirm_or_not, pnl)
for code in SYMS:
    d=load(code); days=sorted(d.keys()); prev_low=None; prev_vol_avg=0.0
    for di,day in enumerate(days):
        bars=byday(d,day)
        if len(bars)<20: continue
        if di>0:
            pb=byday(d,days[di-1])
            if pb: prev_low=min(float(b["low"]) for b in pb); prev_vol_avg=np.mean([float(b.get("vol") or 0) for b in pb])
        if prev_low is None or prev_vol_avg<=0: continue
        buy=find_buy(bars,prev_low,prev_vol_avg)
        if not buy or not is_fri(day): continue
        kind,bp,bi=buy; entry=bp; post=bars[bi+1:]
        if len(post)<6: continue
        v1=t_sell_v1(post); close=float(bars[-1]["close"])
        def pnl(p): return round((p/entry-1)*100,2)
        if v1: friday.append((day, kind, "confirm", pnl(v1["price"])))
        else: friday.append((day, kind, "no", pnl(close)))
def stat(rows, label):
    if not rows: print("  ", label, ": n=0"); return
    p=np.array([x[3] for x in rows]); print("  %s: n=%d avg=%+.2f%% win=%d%% 亏=%d(%.0f%%)" % (label, len(p), np.mean(p), np.mean(p>0)*100, int(np.sum(p<0)), np.sum(p<0)/len(p)*100))
print("== 周五确认制T出+未确认 (基线) ==")
stat(friday, "全部")
print("== 周五按买点类型 ==")
stat([x for x in friday if x[1]=="254"], "254触前低")
stat([x for x in friday if x[1]=="zT"], "正T低吸(-2.5%)")
print("== 只做'确信' = 只统计确认制T出(跳过未确认) ==")
stat([x for x in friday if x[2]=="confirm"], "周五只做确认制T出")
print("== 更保守: 周五只用254且只做确认制T出 ==")
stat([x for x in friday if x[1]=="254" and x[2]=="confirm"], "周五254+确认")
print("== 对照: 若周五完全不碰(去掉周五) ==")
alln=[x for x in friday]
# 去掉周五后的整体(非周五) 已在之前; 仅提示
