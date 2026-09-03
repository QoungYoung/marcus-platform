# -*- coding: utf-8 -*-
"""backtest_wolf_t_consistency_v6.py — 三层卖出(确认T出/day_end/defensive) + 买纪律 近两周一致率"""
import os, sys, json
D='/app/data'; sys.path.insert(0,'/app')
from app.services.wolf_t_rules import zheng_t_buy
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
def defensive(bs, prev):
    st=bs[-1]
    pv=[sum(float(b.get('vol') or 0) for b in p) for p in prev[:5]]
    avg=sum(pv)/len(pv) if pv else 0
    cur=sum(float(b.get('vol') or 0) for b in bs); hi=max(float(b['high']) for b in bs)
    vol_low = avg>0 and cur<=avg*0.8
    fade = hi>0 and float(st['close'])<hi*0.99
    return vol_low and fade
ELIGIBLE=['159516','588170','301018']
DAYS=['20260820','20260821','20260824','20260825','20260826','20260827','20260828','20260831','20260901','20260902','20260903']
WOLF={'20260820':'wait','20260821':'sell_T','20260824':'buy_T','20260825':'buy(液冷)','20260826':'wait','20260827':'sell_T','20260828':'wait','20260831':'wait','20260901':'buy_T+sell','20260902':'buy_T+sell','20260903':'no_semi_T'}
print('v6 三层卖出+买纪律 日级对照')
print('%-10s %-12s | %-8s | %-20s' % ('date','wolf','buy','sell(3层)'))
rows=[]
for d in DAYS:
    bh=[]; sh=[]; shr={}
    for c in ELIGIBLE:
        dc=daily(c); bs=dc.get(d)
        if not bs: continue
        prev=[dc[k] for k in sorted(dc) if k<d and dc[k]][-5:]
        bi=buy_idx(bs)
        conf=t_sell_confirmed(bs,bi)
        if bi is not None and zheng_t_buy(bs,prev)[0]:
            bh.append(c)
            if conf: sh.append(c+':确认T出'); shr.setdefault(c,[.1,0])
            else: sh.append(c+':day_end'); shr.setdefault(c,[0,1])
        if defensive(bs,prev): sh.append(c+':defensive'); shr.setdefault(c,[0,0])
    bh=bh[:1]; sh=sh[:1]
    rows.append((d,WOLF[d],bh,sh))
    print('%-10s %-12s | %-8s | %-20s' % (d,WOLF[d][:12],(';'.join(bh) if bh else '-'),(';'.join(sh) if sh else '-')))
wb=[r[0] for r in rows if 'buy' in r[1]]; ws=[r[0] for r in rows if 'sell' in r[1]]
ob=[r[0] for r in rows if r[2]]; os_=[r[0] for r in rows if r[3]]
def rate(w,o,label):
    tp=sum(1 for x in w if x in o); print('%s: 召回=%.0f%%(%d/%d) 精度=%.0f%%(%d/%d)' % (label, tp/len(w)*100, tp,len(w), tp/len(o)*100 if o else 0, tp,len(o)))
print(); rate(wb,ob,'买'); rate(ws,os_,'卖')
print('狼大买',wb,'卖',ws,'| 我们买',ob,'卖',os_)
