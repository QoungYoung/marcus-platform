# -*- coding: utf-8 -*-
"""backtest_recent_two_weeks.py — 假设持仓=狼大, 用已落地逻辑回放 08-20~09-03
买腿: 253(大盘5min急杀≤-0.4%) + 254(触前低缩量) + 分步回补(vr≤1.2, 3日内≤2次)
卖腿: 分时T出(放量反弹→第一次分时高点→停量→二次拉升无量不过前高, 5min)
"""
import os, sys, json
from datetime import date, timedelta
DATA='/app/data'; OUT='/app/data/recent_sync'
def load(p): 
    try: return json.load(open(os.path.join(DATA,p),encoding='utf-8'))
    except: return {}
def ls(p):
    try: return json.load(open(os.path.join(OUT,p),encoding='utf-8'))
    except: return {}

def bars_sorted(bars): return sorted(bars, key=lambda b: str(b.get('time') or b.get('trade_time')))
def day_low(bars): return min(float(b['low']) for b in bars)
def day_close(bars):
    bs=bars_sorted(bars); return float(bs[-1]['close']) if bs else 0.0
def cum_vols(bars):
    out=[];s=0.0
    for b in bars_sorted(bars): s+=float(b.get('vol') or 0); out.append(s)
    return out
def cum_amts(bars):
    out=[];s=0.0
    for b in bars_sorted(bars): s+=float(b.get('amount') or 0); out.append(s)
    return out
def hm_of(t):
    try: return int(str(t)[11:13])*100+int(str(t)[14:16]) if len(str(t))>=16 else int(str(t)[8:10])*100+int(str(t)[10:12])
    except: return 0
def time_ok(t): return 945<=hm_of(t)<=1440
def find_253(idx_bars):
    bs=bars_sorted(idx_bars); prev=None
    for b in bs:
        c=float(b['close'])
        if prev and prev>0 and (c-prev)/prev*100<=-0.4: return str(b.get('time') or b.get('trade_time'))
        prev=c
    return None
def near_limit_down(cl, prevdc): return bool(prevdc and (cl/prevdc-1)*100<=-9.5)
def bar_at_or_after(bars,t):
    bs=bars_sorted(bars); w=str(t)
    for b in bs:
        if str(b.get('time') or b.get('trade_time'))>=w: return b
    return None
def find_254(dm, days, di, thresh_vr=0.7):
    if di<=0: return None
    pl=day_low(dm[days[di-1]])
    if pl<=0: return None
    cur=dm[days[di]]; cbs=bars_sorted(cur)
    if len(cbs)<2: return None
    base=[days[j] for j in range(max(0,di-5),di)]
    base_cums=[cum_vols(dm[dd]) for dd in base]
    cums=cum_vols(cur); amts=cum_amts(cur); rlow=10**18
    for i,b in enumerate(cbs):
        lo=float(b['low']); cl=float(b['close']); rlow=min(rlow,lo)
        if rlow<=pl*1.005 and i>=1:
            vals=[bc[i] if i<len(bc) else (bc[-1] if bc else 0) for bc in base_cums]
            avg=sum(vals)/len(vals) if vals else 0
            cum=cums[i]
            vr=(cum/avg) if avg>0 else 0
            vwap=(amts[i]/cum) if cum>0 else 0
            if vr<=thresh_vr and vwap>0 and cl>vwap:
                return {'time':str(b.get('time') or b.get('trade_time')),'close':cl,'vr':round(vr,2)}
    return None

def intraday_t_sell5(bars, look=8):
    """5min 分时T出: 放量反弹→第一次分时高点→停量→二次拉升无量不过前高→(bool, 时间, desc)"""
    closes=[float(b['close']) for b in bars_sorted(bars)]
    vols=[float(b.get('vol') or 0) for b in bars_sorted(bars)]
    n=len(closes)
    if n < look*4+4: return False,'', '数据不足'
    start=None
    for i in range(look, n-look-2):
        base=sum(vols[max(0,i-look):i])/look
        if base>0 and vols[i]>base*1.3: start=i; break
    if start is None: return False,'','无放量反弹'
    seg=closes[start:n-look]; hi=max(seg); hi_idx=start+seg.index(hi)
    if hi_idx<start+2: return False,'','高点太近'
    vol_after=sum(vols[hi_idx+1:hi_idx+1+look])/look if hi_idx+look<n else 0
    vol_before=sum(vols[max(start,hi_idx-look):hi_idx])/max(1,(hi_idx-max(start,hi_idx-look)))
    stopped = vol_before>0 and vol_after<vol_before*0.8
    after=closes[hi_idx+1:]; second_hi=max(after) if after else 0
    second_up = second_hi>hi*0.98 and second_hi<hi*1.005
    trig = stopped and second_up
    t= str((bars_sorted(bars)[n-1].get('time') or ''))
    return bool(trig), t, f"第一高点{hi:.3f} 第二高点{second_hi:.3f}{'停量' if stopped else '未停量'}{'二次冲高' if second_up else ''}"

# ---- build index ----
idx_all={}
raw=load('index_5min_dh.json')
for k,v in (raw or {}).items(): idx_all[str(k).replace('-','')]=v
sina=ls('index5_sina.json')
for k,v in (sina or {}).items():
    d=str(k).replace('-','')
    if d not in idx_all: idx_all[d]=v   # only add days beyond real (09-02/03)
idx_days=sorted(idx_all)
print('idx days',len(idx_days),idx_days[0],idx_days[-1],flush=True)

CODES={'159516.SZ':'半导体设备ETF国泰','588170.SH':'科创半导体ETF华夏','301018.SZ':'申菱环境液冷','002165.SZ':'红宝丽化工','603757.SH':'大元泵业化工','002945.SZ':'华林证券券商'}
WIN=set(w for w in idx_days if '20260819'<=w<='20260903')
rows=[]
for code,name in CODES.items():
    dm=ls(code.split('.')[0]+'.json')
    days=sorted(dm.keys())
    last254=-99; refills=0
    for di,d in enumerate(days):
        if d not in WIN: continue
        idxb=idx_all.get(d); dc=dm[d]
        act={}
        if idxb:
            t253=find_253(idxb)
            if t253 and time_ok(t253):
                # 253买(任一标的): 记录
                act['253']=t253
        r254=find_254(dm,days,di)
        if r254:
            act['254']=r254['time']; last254=di; refills=0
        elif 0<di-last254<=3 and refills<2:
            rr=find_254(dm,days,di,thresh_vr=1.2)
            if rr: act['refill']=rr['time']; refills+=1
        tsel,tt,tdesc=intraday_t_sell5(dc)
        if tsel: act['Tsell']=tt
        if act:
            rows.append({'date':d,'code':code,'name':name,'time':act})
for r in rows: print(r['date'],r['code'],r['name'],json.dumps(r['time'],ensure_ascii=False),flush=True)
# aggregated per date
from collections import defaultdict
by=defaultdict(list)
for r in rows: by[r['date']].append((r['name'],r['time']))
print('\\n== 按日期聚合 ==',flush=True)
for d in sorted(by):
    print(d, ' '.join(f"{nm}:{json.dumps(t,ensure_ascii=False)}" for nm,t in by[d]),flush=True)
print('DONE',len(rows),'触发行',flush=True)
