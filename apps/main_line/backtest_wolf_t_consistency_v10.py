# -*- coding: utf-8 -*-
"""backtest_wolf_t_consistency_v10.py — 规则打分 + 计划/关键位因子 校准(买精度)"""
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
def ma20(d):
    rows=sorted(load('index_daily_000001.json'),key=lambda x:int(str(x['trade_date']).replace('-','')))
    rows=[r for r in rows if int(str(r['trade_date']).replace('-',''))<=int(d)]
    return sum(float(r['close']) for r in rows[-20:])/20 if len(rows)>=20 else None
def idx_low(d):
    for p in ['index_5min_dh.json']:
        for k,v in load(p).items():
            if str(k).replace('-','')==d:
                bs=sorted(v,key=lambda x:str(x.get('time') or x.get('trade_time')))
                return min(float(b['low']) for b in bs) if bs else None
    for k,v in (load('recent_sync/index5_sina.json') or {}).items():
        if str(k).replace('-','')==d:
            bs=sorted(v,key=lambda x:str(x.get('time') or x.get('trade_time')))
            return min(float(b['low']) for b in bs) if bs else None
    return None
def tech_dd(d):
    best=None
    for c in ['159516','588170']:
        dc=daily(c); bs=dc.get(d)
        if not bs: continue
        hi=max(float(b['high']) for b in bs); lo=min(float(b['low']) for b in bs)
        dd=(lo/hi-1)*100 if hi>0 else 0
        best=dd if best is None else min(best,dd)
    return best
def pos_pct(dc, d):
    ks=sorted(k for k in dc if k<=d and dc[k])
    if len(ks)<20: return 50
    vals=[float(dc[k][-1]['close']) for k in ks[-60:]]
    cur=float(dc[ks[-1]][-1]['close']); lo=min(vals); hi=max(vals)
    return (cur-lo)/(hi-lo)*100 if hi>lo else 50
WAVE={'20260820':'t_only','20260821':'t_only','20260824':'side','20260825':'side','20260826':'t_only','20260827':'t_only','20260828':'t_only','20260831':'t_only','20260901':'t_only','20260902':'side','20260903':'t_only'}
WOLF={'20260820':'wait','20260821':'sell_T','20260824':'buy','20260825':'buy','20260826':'wait','20260827':'sell_T','20260828':'wait','20260831':'wait','20260901':'buy','20260902':'buy','20260903':'no'}
DAYS=['20260820','20260821','20260824','20260825','20260826','20260827','20260828','20260831','20260901','20260902','20260903']
def score(d):
    c='159516'; dc=daily(c); bs=dc.get(d)
    if not bs: return None
    prev=[dc[k] for k in sorted(dc) if k<d and dc[k]][-5:]
    if not prev or not zheng_t_buy(bs,prev)[0]: return None
    wv = 1.0 if WAVE[d] in ('t_only','side') else 0.3
    m20=ma20(d); low=idx_low(d)
    keyd=max(0.0,(m20-low)/m20*100) if m20 and low else 0
    key=min(1.0, keyd/1.0)
    pv=[sum(float(b.get('vol') or 0) for b in p) for p in prev[:5]]; avg=sum(pv)/len(pv) if pv else 0
    cur=sum(float(b.get('vol') or 0) for b in bs); vol=1.0 if ( avg>0 and cur<=avg*0.9 ) else 0.2
    # 计划/关键位因子: 关键位回补(指数破MA20>0.3%) 或 科技急跌<=-3% 或 主题计划低位
    dd=tech_dd(d)
    plan = 1.0 if (keyd>=0.3 or (dd is not None and dd<=-3.0)) else 0.3
    # 位置分位(低) 用 半导体etf
    pp=pos_pct(dc,d); pos=1.0 if pp<40 else (0.6 if pp<60 else 0.2)
    main=1.0
    s=0.15*wv+0.20*key+0.15*vol+0.25*plan+0.15*pos+0.10*main
    return s, keyd, dd, pp
byday={}
for d in DAYS:
    r=score(d)
    byday[d]=r
    if r: print(d,'wolf=%s'%WOLF[d].ljust(5),'score=%.2f keyd=%.2f dd=%.1f pp=%.0f'%(r[0],r[1],r[2],r[3]))
wolf_buy=['20260824','20260825','20260901','20260902']
best=None
for thr in [round(x/100,2) for x in range(45,91,2)]:
    ob=[d for d in DAYS if byday[d] and byday[d][0]>=thr]
    tp=sum(1 for w in wolf_buy if w in ob)
    rec=tp/len(wolf_buy); prec=tp/len(ob) if ob else 0
    if rec>=0.99 and (best is None or prec>best[2]): best=(thr,rec,prec,ob)
print()
print('最优阈值=%.2f 召回=%.0f%% 精度=%.0f%%'%(best[0],best[1]*100,best[2]*100))
print('我们买日=',best[3]); print('狼大买日=',wolf_buy)
