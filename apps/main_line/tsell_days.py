# -*- coding: utf-8 -*-
import json, os, datetime
import numpy as np
DATA = os.environ.get("DATA_DIR","data")
SYMS = ["000977","002165","002371","002945","159516","300308","301018","588170","601138","603259","603757","688981"]
def load(code):
    try: return json.load(open(os.path.join(DATA,"recent_sync",code+".json"),encoding="utf-8"))
    except Exception: return {}
def byday(d,day): return sorted(d.get(day,[]), key=lambda b: str(b.get("time") or b.get("trade_time")))
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
alldays=set(); trades=[]  # (day, symbol, pnl)
for code in SYMS:
    d=load(code); days=sorted(d.keys()); prev_low=None; prev_vol_avg=0.0
    for di,day in enumerate(days):
        bars=byday(d,day)
        alldays.add(day)
        if len(bars)<20: continue
        if di>0:
            pb=byday(d,days[di-1])
            if pb: prev_low=min(float(b["low"]) for b in pb); prev_vol_avg=np.mean([float(b.get("vol") or 0) for b in pb])
        if prev_low is None or prev_vol_avg<=0: continue
        buy=find_buy(bars,prev_low,prev_vol_avg)
        if not buy: continue
        kind,bp,bi=buy; entry=bp; post=bars[bi+1:]
        if len(post)<6: continue
        v1=t_sell_v1(post); close=float(bars[-1]["close"])
        pnl=round(((v1["price"] if v1 else close)/entry-1)*100,2)
        trades.append((day, code, pnl))
tradedays=set(x[0] for x in trades)
print("覆盖交易日(近recent_sync, 可交易有数据): %d 天" % len(alldays))
print("有交易的天数(至少一笔): %d 天" % len(tradedays))
print("总交易笔数: %d" % len(trades))
print("总收益(每笔%之和, 非复利): %+.1f%%" % sum(x[2] for x in trades))
print("每笔均值: %+.2f%% | 平均每交易日(有交易)收益和: %+.1f%%" % (np.mean([x[2] for x in trades]), sum(x[2] for x in trades)/len(tradedays) if tradedays else 0))
# 数据窗口范围
all_days_sorted=sorted(alldays)
print("数据窗口: %s ~ %s" % (all_days_sorted[0], all_days_sorted[-1]))
# per-day distribution
from collections import Counter
perday=Counter(x[0] for x in trades)
print("单日交易笔数分布: 最多%d笔/天, 平均%.1f笔/有交易日" % (max(perday.values()), np.mean(list(perday.values()))))
