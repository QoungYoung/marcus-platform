# -*- coding: utf-8 -*-
"""backtest_wolf_t_consistency_v8.py — + 择机判断(指数触及MA20/关键位 or 主题低位) 精度"""
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
def idx_daily_up(d):
    rows=sorted(load('index_daily_000001.json'),key=lambda x:int(str(x['trade_date']).replace('-','')))
    return [r for r in rows if int(str(r['trade_date']).replace('-',''))<=int(d)]
def idx_low_5min(d):
    m=load('index_5min_dh.json')
    for k,v in m.items():
        if str(k).replace('-','')==d:
            bs=sorted(v,key=lambda x:str(x.get('time') or x.get('trade_time')))
            return min(float(b['low']) for b in bs) if bs else None
    m=load('recent_sync/index5_sina.json')
    for k,v in (m or {}).items():
        if str(k).replace('-','')==d:
            bs=sorted(v,key=lambda x:str(x.get('time') or x.get('trade_time')))
            return min(float(b['low']) for b in bs) if bs else None
    return None
def ma20(d):
    rows=idx_daily_up(d)
    if len(rows)<20: return None
    return sum(float(r['close']) for r in rows[-20:])/20
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
ELIGIBLE=['159516','588170','301018']
DAYS=['20260820','20260821','20260824','20260825','20260826','20260827','20260828','20260831','20260901','20260902','20260903']
WOLF={'20260820':'wait','20260821':'sell_T','20260824':'buy_T','20260825':'buy(液冷)','20260826':'wait','20260827':'sell_T','20260828':'wait','20260831':'wait','20260901':'buy_T+sell','20260902':'buy_T+sell','20260903':'no_semi_T'}
print('v8 择机判断(指数触/破MA20 或 主题低位) 日级对照')
print('%-10s %-12s idxLow/MA20 | %-8s | %-22s' % ('date','wolf','buy','sell'))
rows=[]
dc301=daily('301018')
for d in DAYS:
    bh=[]; sh=[]
    low=idx_low_5min(d); m20=ma20(d)
    key_hit = low is not None and m20 and low <= m20*1.005   # 指数触/破MA20关键位
    # 主题低位(计划兵): 301018 当日位置分位<40%
    pos=None
    dc=dc301; bs=dc.get(d)
    if bs:
        pv=[float(dc[k][-1]['close']) for k in sorted(dc) if k<d and dc[k]][-60:]
        if pv:
            cur=float(bs[-1]['close']); lo=min(pv); hi=max(pv)
            pos=(cur-lo)/(hi-lo)*100 if hi>lo else 50
    theme_low = pos is not None and pos<40
    for c in ELIGIBLE:
        dc_=daily(c); bs_=dc_.get(d)
        if not bs_: continue
        prev_=[dc_[k] for k in sorted(dc_) if k<d and dc_[k]][-5:]
        bi=buy_idx(bs_); conf=t_sell_confirmed(bs_,bi)
        if bi is not None and zheng_t_buy(bs_,prev_)[0] and (key_hit or theme_low):
            bh.append(c)
            sh.append((c+':确认T出' if conf else c+':day_end'))
        if conf: pass
    bh=bh[:1]; sh=sh[:1]
    rows.append((d,WOLF[d],bh,sh))
    print('%-10s %-12s %7.2f/%6.2f | %-8s | %-22s' % (d,WOLF[d][:12], low or 0, m20 or 0, (';'.join(bh) if bh else '-'),(';'.join(sh) if sh else '-')))
wb=[r[0] for r in rows if 'buy' in r[1]]; ws=[r[0] for r in rows if 'sell' in r[1]]
ob=[r[0] for r in rows if r[2]]; os_=[r[0] for r in rows if r[3]]
def rate(w,o,label):
    tp=sum(1 for x in w if x in o); print('%s: 召回=%.0f%%(%d/%d) 精度=%.0f%%(%d/%d)' % (label, tp/len(w)*100, tp,len(w), tp/len(o)*100 if o else 0, tp,len(o)))
print(); rate(wb,ob,'买'); rate(ws,os_,'卖')
print('狼大买',wb,'卖',ws,'| 我们买',ob,'卖',os_)
