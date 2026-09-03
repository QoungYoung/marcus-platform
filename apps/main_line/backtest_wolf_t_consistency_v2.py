# -*- coding: utf-8 -*-
"""backtest_wolf_t_consistency_v2.py — 近两周 纪律过滤后 我们做T信号 vs 狼大 一致率"""
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
            if dry and closes[j]<hi*up and closes[j]>closes[bi]*1.005 and any(closes[k]>=hi*0.98 for k in range(hi_idx+1,j+1)):
                return True
    return False
# 纪律: 仅狼大T标的 + 3点目标(日高-买入>=2.5%) + 当日首信号
ELIGIBLE=['159516','588170','301018']
TARGET=2.5
DAYS=['20260820','20260821','20260824','20260825','20260826','20260827','20260828','20260831','20260901','20260902','20260903']
WOLF={'20260820':'wait','20260821':'sell_T','20260824':'buy_T','20260825':'buy(液冷)','20260826':'wait','20260827':'sell_T','20260828':'wait','20260831':'wait','20260901':'buy_T+sell','20260902':'buy_T+sell','20260903':'no_semi_T'}
print('纪律过滤后 日级对照 (标的=159516/588170/301018, 3点目标>=%.1f%%)' % TARGET)
print('%-10s %-12s | %-22s | %-16s' % ('date','wolf','buy候选(正T+3点)','sell(确认T出/倒T)'))
rows=[]
for d in DAYS:
    bh=[]; sh=[]
    for c in ELIGIBLE:
        dc=daily(c); bs=dc.get(d)
        if not bs: continue
        prev=[dc[k] for k in sorted(dc) if k<d and dc[k]][-5:]
        bi=buy_idx(bs)
        if bi is not None:
            buy=float(bs[bi]['low']); dayhi=max(float(b['high']) for b in bs)
            room=(dayhi/buy-1)*100
            if zheng_t_buy(bs,prev)[0] and room>=TARGET:
                bh.append(c)
            if t_sell_confirmed(bs,bi): sh.append(c+':T出')
        if dao_t_sell(bs,prev): sh.append(c+':倒T')
    bh=sorted(set(bh)); sh=sorted(set(sh))
    rows.append((d,WOLF[d],bh,sh))
    print('%-10s %-12s | %-22s | %-16s' % (d,WOLF[d][:12],(';'.join(bh) if bh else '-'),(';'.join(sh) if sh else '-')))
wb=[r[0] for r in rows if 'buy' in r[1]]; ws=[r[0] for r in rows if 'sell' in r[1]]
ob=[r[0] for r in rows if r[2]]; os_=[r[0] for r in rows if r[3]]
def rate(w,o,label):
    if not o: print(label,'无信号',0); return
    tp=sum(1 for x in w if x in o)
    print('%s: 召回=%.0f%%(%d/%d) 精度=%.0f%%(%d/%d)' % (label, tp/len(w)*100, tp,len(w), tp/len(o)*100, tp,len(o)))
print()
rate(wb,ob,'买')
rate(ws,os_,'卖')
print('狼大买日',wb,'狼大卖日',ws)
print('我们买日',ob,'我们卖日',os_)
