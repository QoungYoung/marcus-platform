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
def hm(b):
    t=str(b.get("time") or b.get("trade_time") or "")
    return int(t[8:12]) if len(t)>=12 and t.isdigit() else 0
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
def run():
    allc=[]; alld=[]  # (day, kind, pnl)
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
            if not buy: continue
            kind,bp,bi=buy; entry=bp; post=bars[bi+1:]
            if len(post)<6: continue
            v1=t_sell_v1(post); close=float(bars[-1]["close"])
            def pnl(p): return (p/entry-1)*100
            if v1:
                if is_fri(day): allc.append((day,kind,round(pnl(v1["price"]),2)))
            else:
                if is_fri(day): alld.append((day,kind,round(pnl(close),2)))
    return allc, alld
allc, alld = run()
def stat(v):
    p=np.array([x[2] for x in v]); return "n=%d avg=%+.2f%% win=%d%% 亏=%d(%.0f%%)" % (len(v), np.mean(p), np.mean(p>0)*100, int(np.sum(p<0)), np.sum(p<0)/len(p)*100)
print("== 周五 confirm_T出 (已卖出) ==")
print("  ", stat(allc))
print("== 周五 未确认(day_end收盘) 基线 ==")
print("  ", stat(alld))
# 周五未确认 → @14:00 提前离场: 用bars再取
# 需重新遍历拿14:00价; 简化: 单独收集
# 为准确, 重跑收集@1400
def run_with_early(tgt):
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
            if not buy: continue
            kind,bp,bi=buy; entry=bp; post=bars[bi+1:]
            if len(post)<6: continue
            v1=t_sell_v1(post)
            if v1 or not is_fri(day): continue
            close=float(bars[-1]["close"]); ex=None
            for b in reversed(bars[bi+1:]):
                if hm(b)<=tgt: ex=float(b["close"]); break
            ex = ex if ex is not None else close
            yield (day, kind, (ex/entry-1)*100)
early=list(run_with_early(1400))
print("== 周五 未确认 → 提前@14:00 离场 ==")
print("  n=%d avg=%+.2f%% win=%d%% 亏=%d(%.0f%%)" % (len(early), np.mean([x[2] for x in early]), np.mean(np.array([x[2] for x in early])>0)*100, sum(1 for x in early if x[2]<0), sum(1 for x in early if x[2]<0)/len(early)*100 if early else 0))
# 周五整体(confirm + 未确认) 基线 vs 14:00规则
base_p = [x[2] for x in allc]+[x[2] for x in alld]
rule_p = [x[2] for x in allc]+[x[2] for x in early]
print("== 周五整体(confirm+未确认) 基线 vs @14:00规则 ==")
print("  基线 day_end:", "n=%d avg=%+.2f%% win=%d%% 亏=%d(%.0f%%)" % (len(base_p), np.mean(base_p), np.mean(np.array(base_p)>0)*100, sum(1 for x in base_p if x<0), sum(1 for x in base_p if x<0)/len(base_p)*100))
print("  @14:00规则:   ", "n=%d avg=%+.2f%% win=%d%% 亏=%d(%.0f%%)" % (len(rule_p), np.mean(rule_p), np.mean(np.array(rule_p)>0)*100, sum(1 for x in rule_p if x<0), sum(1 for x in rule_p if x<0)/len(rule_p)*100))
