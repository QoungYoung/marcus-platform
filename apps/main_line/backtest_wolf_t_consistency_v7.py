# -*- coding: utf-8 -*-
"""backtest_wolf_t_consistency_v7.py — + defensive_t_reduce_index(科技不跟) 近两周一致率"""
import os, sys, json
D='/app/data'; sys.path.insert(0,'/app')
from app.services.wolf_t_rules import zheng_t_buy
def load(p):
    try: return json.load(open(os.path.join(D,p),encoding='utf-8'))
    except: return {}
def daily(code):
    out={}
    for root in ['recent_sync','stock_5m_bt']:
        p=os.path.join(D,root,code+'.json')
        try: d=json.load(open(p,encoding='utf-8'))
        except: continue
        for k,v in d.items():
            bs=sorted(v,key=lambda x:str(x.get('time') or x.get('trade_time')))
            if bs: out.setdefault(k,bs)
    return out
def day_hi(dc, d):
    bs=dc.get(d); return max(float(b['high']) for b in bs) if bs else None
# index 5min high per day
def idx_days():
    out={}
    for p in ['index_5min_dh.json']:
        d=load(p)
        for k,v in d.items():
            bs=sorted(v,key=lambda x:str(x.get('time') or x.get('trade_time')))
            if bs: out.setdefault(str(k).replace('-',''), max(float(b['high']) for b in bs))
    d=load('recent_sync/index5_sina.json')
    for k,v in (d or {}).items():
        bs=sorted(v,key=lambda x:str(x.get('time') or x.get('trade_time')))
        if bs: out.setdefault(str(k).replace('-',''), max(float(b['high']) for b in bs))
    return out
IDX=idx_days()
WAVE={'20260820':'t_only','20260821':'t_only','20260824':'side','20260825':'side','20260826':'t_only','20260827':'t_only','20260828':'t_only','20260831':'t_only','20260901':'t_only','20260902':'side','20260903':'t_only'}
def buy_idx(bs, thr=2.5):
    if len(bs)<6: return None
    hi=max(float(b['high']) for b in bs); run=10**18
    for i,b in enumerate(bs):
        run=min(run,float(b['low']))
        if hi>0 and (run/hi-1)*100<=-thr: return i
    return None
def t_sell_confirmed(bs, bi, look=5, vol_dry=0.8, up=1.005):
    if bi is None or bi+look+2>=len(bs): return False
    closes=[float(b['close']) for b in bs]; vols=[float(b.get('vol') or 0) for b in bs]
    hi=closes[bi+1]; hi_idx=bi+1
    for j in range(bi+2,len(bs)):
        if closes[j]>hi: hi=closes[j]; hi_idx=j
        if j>=hi_idx+2:
            va=sum(vols[j-look:j+1])/look if j>=look else 0
            vb=sum(vols[hi_idx-look:hi_idx])/look if hi_idx>=look else 0
            dry=vb>0 and va<vb*vol_dry
            if dry and closes[j]<=hi*up and closes[j]>closes[bi]*1.005 and any(closes[k]>=hi*0.98 for k in range(hi_idx+1,j+1)): return True
    return False
def defense_idx(d, tech_hi_now, tech_hi_prev, idx_hi_now, idx_hi_prev):
    if WAVE.get(d) not in ('t_only','side'): return False
    idx_high = idx_hi_now is not None and idx_hi_prev and idx_hi_now >= idx_hi_prev*0.998
    tech_lag  = tech_hi_now is not None and tech_hi_prev and tech_hi_now < tech_hi_prev*0.998
    return bool(idx_high and tech_lag)
ELIGIBLE=['159516','588170','301018']
DAYS=['20260820','20260821','20260824','20260825','20260826','20260827','20260828','20260831','20260901','20260902','20260903']
WOLF={'20260820':'wait','20260821':'sell_T','20260824':'buy_T','20260825':'buy(液冷)','20260826':'wait','20260827':'sell_T','20260828':'wait','20260831':'wait','20260901':'buy_T+sell','20260902':'buy_T+sell','20260903':'no_semi_T'}
print('v7 +defensive_index(科技不跟) 日级对照')
print('%-10s %-12s | %-8s | %-22s' % ('date','wolf','buy','sell'))
rows=[]
dc159=daily('159516'); dc588=daily('588170')
for d in DAYS:
    bh=[]; sh=[]
    for c in ELIGIBLE:
        dc=daily(c); bs=dc.get(d)
        if not bs: continue
        prev=[dc[k] for k in sorted(dc) if k<d and dc[k]][-5:]
        bi=buy_idx(bs)
        conf=t_sell_confirmed(bs,bi)
        if bi is not None and zheng_t_buy(bs,prev)[0]:
            bh.append(c)
            sh.append((c+':确认T出' if conf else c+':day_end'))
    # 指数/科技结构级: 科技(159516/588170) 是否 上证新高而科技不跟
    tech_now=max([day_hi(dc159,d), day_hi(dc588,d)])
    prev5=[max([day_hi(dc159,k), day_hi(dc588,k)]) for k in sorted(dc159) if k<d][-5:]
    tech_prev=max(prev5) if prev5 else 0
    idx_now=IDX.get(d); idx_prev=max([IDX[k] for k in sorted(IDX) if k<d][-5:] or [0])
    di=defense_idx(d, tech_now, tech_prev, idx_now, idx_prev)
    if di: sh.append('defensive_index')
    bh=bh[:1]; sh=sh[:1]
    rows.append((d,WOLF[d],bh,sh))
    print('%-10s %-12s | %-8s | %-22s' % (d,WOLF[d][:12],(';'.join(bh) if bh else '-'),(';'.join(sh) if sh else '-')))
wb=[r[0] for r in rows if 'buy' in r[1]]; ws=[r[0] for r in rows if 'sell' in r[1]]
ob=[r[0] for r in rows if r[2]]; os_=[r[0] for r in rows if r[3]]
def rate(w,o,label):
    tp=sum(1 for x in w if x in o); print('%s: 召回=%.0f%%(%d/%d) 精度=%.0f%%(%d/%d)' % (label, tp/len(w)*100, tp,len(w), tp/len(o)*100 if o else 0, tp,len(o)))
print(); rate(wb,ob,'买'); rate(ws,os_,'卖')
print('狼大买',wb,'卖',ws,'| 我们买',ob,'卖',os_)
