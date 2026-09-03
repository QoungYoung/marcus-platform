# -*- coding: utf-8 -*-
"""backtest_wolf_t_consistency.py — 近两周 我们做T信号(wolf_t_rules) vs 狼大买卖 日级一致率"""
import os, sys, json
D='/app/data'
sys.path.insert(0,'/app')
from app.services.wolf_t_rules import zheng_t_buy, dao_t_sell
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
def buy_idx(bs, thr=2.5):
    if len(bs)<6: return None
    hi=max(float(b['high']) for b in bs); run=10**18
    for i,b in enumerate(bs):
        run=min(run,float(b['low']))
        if hi>0 and (run/hi-1)*100<=-thr: return i
    return None
def t_sell_confirmed(bs, buy_idx, look=5, vol_dry=0.8, up=1.005):
    if buy_idx is None or buy_idx+look+2>=len(bs): return False
    closes=[float(b['close']) for b in bs]; vols=[float(b.get('vol') or 0) for b in bs]
    hi=closes[buy_idx+1]; hi_idx=buy_idx+1
    for j in range(buy_idx+2,len(bs)):
        if closes[j]>hi: hi=closes[j]; hi_idx=j
        if j>=hi_idx+2:
            va=sum(vols[j-look:j+1])/look if j>=look else 0
            vb=sum(vols[hi_idx-look:hi_idx])/look if hi_idx>=look else 0
            dry=vb>0 and va<vb*vol_dry
            not_break=closes[j]<hi*up
            above=closes[j]>closes[buy_idx]*1.005
            if dry and not_break and above and any(closes[k]>=hi*0.98 for k in range(hi_idx+1,j+1)):
                return True
    return False
PROXIES={'半导体':['159516','588170'],'液冷':['301018'],'药明':['603259']}
DAYS=['20260820','20260821','20260824','20260825','20260826','20260827','20260828','20260831','20260901','20260902','20260903']
WOLF={'20260820':'wait','20260821':'sell_T','20260824':'buy_T','20260825':'buy(液冷)','20260826':'wait',
      '20260827':'sell_T','20260828':'wait','20260831':'wait','20260901':'buy_T+sell','20260902':'buy_T+sell','20260903':'no_semi_T'}
print('%-10s %-14s | %-26s | %-26s' % ('date','wolf','our buy候选(正T)','our sell(确认T出/倒T)'))
row=[]
for d in DAYS:
    buyhits=[]; sellhits=[]
    for theme,codes in PROXIES.items():
        for c in codes:
            dc=daily(c); bs=dc.get(d)
            if not bs: continue
            prev=[dc[k] for k in sorted(dc) if k<d and dc[k]][-5:]
            bi=buy_idx(bs)
            if bi is not None:
                buyhits.append(c)
                if t_sell_confirmed(bs,bi): sellhits.append(c+':确认T出')
            b2,_=zheng_t_buy(bs, prev)
            if b2: buyhits.append(c+'(z)')
            d2,_=dao_t_sell(bs, prev)
            if d2: sellhits.append(c+':倒T')
    buyhits=sorted(set(buyhits)); sellhits=sorted(set(sellhits))
    row.append((d,WOLF[d],buyhits,sellhits))
    print('%-10s %-14s | %-26s | %-26s' % (d,WOLF[d][:14],(';'.join(buyhits) if buyhits else '-'),(';'.join(sellhits) if sellhits else '-')))
wb=[r[0] for r in row if 'buy' in r[1]]; ws=[r[0] for r in row if 'sell' in r[1]]
ob=[r[0] for r in row if r[2]]; os_=[r[0] for r in row if r[3]]
def p(w,o):
    if not o: return '0/0'
    tp=sum(1 for x in w if x in o); return '%d/%d' % (tp,len(o))
print()
print('狼大买日:', wb, '| 我们正T候选日:', ob, '| 召回 hit=', p(wb,ob))
print('狼大卖日:', ws, '| 我们卖出信号日:', os_, '| 召回 hit=', p(ws,os_))
