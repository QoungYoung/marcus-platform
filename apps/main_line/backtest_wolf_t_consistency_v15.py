# -*- coding: utf-8 -*-
"""backtest_wolf_t_consistency_v15.py — 基线(0.998/0.998) vs 收紧(0.998/0.98) 的 index 与复合(confirm∪quote∪index) 一致率"""
import sys, os, json
sys.path.insert(0, '/app')
from app.services.wolf_t_rules import defensive_t_reduce_quote, t_cycle_pnl
import requests, urllib3
urllib3.disable_warnings()
DH='http://datahubco.com/app-api/openapi/v1/tushare'; DK='dba548a206a453c197f9175189b757374fa6db9554bb29e69efea127'
PM='https://pcd.mobcvb.cn/tushare/pro'; PK='tsr_1FjRkziz3M7m0aLcTk0ZgnK03__xO3EYq0ZdwQqdwSE'
def sw_l1(sym):
    r=requests.get(f'{DH}/index_member_all',params={'ts_code':sym},headers={'X-API-Key':DK},timeout=40)
    dd=r.json().get('data') or {}; it=dd.get('items') or []; f=dd.get('fields') or []
    if not it or not f: return None
    fm={f[i]:i for i in range(len(f))}
    return it[0][fm['l1_code']] if 'l1_code' in fm else None
def sw_high(idx,s,e):
    r=requests.get(f'{PM}/sw_daily',params={'ts_code':idx,'start_date':s,'end_date':e},headers={'X-API-Key':PK},verify=False,timeout=30)
    dd=r.json().get('data') or {}; it=dd.get('items') or []; f=dd.get('fields') or []
    fm={f[i]:i for i in range(len(f))}; out={}
    if fm and it:
        for x in it:
            td=x[fm['trade_date']]; hi=x[fm['high']]
            if td is None or hi is None: continue
            out[str(td).replace('-','')]=float(hi)
    return out
def load(code):
    out={}
    for root in ['recent_sync','stock_5m_bt']:
        p='/app/data/'+root+'/'+code+'.json'
        try: d=json.load(open(p,encoding='utf-8'))
        except Exception: continue
        for k,v in d.items():
            bs=sorted(v,key=lambda x:str(x.get('time') or x.get('trade_time')))
            if bs: out.setdefault(k,bs)
    return out
def dstat(bs):
    hs=[float(b['high']) for b in bs]
    return {'close':float(bs[-1]['close']),'high':max(hs),'low':min(float(b['low']) for b in bs),
            'vol':sum(float(b.get('vol') or 0) for b in bs)}
WOLF_SELL={'20260821','20260827','20260901','20260902'}
DAYS=['20260820','20260821','20260824','20260825','20260826','20260827','20260828','20260831','20260901','20260902','20260903']
PROX=['603259','301018','688981','002945','002165']
swmap={c:sw_l1(c+'.SH' if c[0]=='6' else c+'.SZ') for c in PROX}
swdaily={c:sw_high(l1,'20260818','20260903') for c,l1 in swmap.items() if l1}
sig={}
for d in DAYS:
    confirm=set(); quote=set(); hi={}; hiprev={}
    for c in PROX:
        dc=load(c); bs=dc.get(d)
        if not bs: continue
        st=dstat(bs); prev=[dstat(dc[k]) for k in sorted(dc) if k<d and dc[k]][-5:]
        if not prev: continue
        try:
            cyc=t_cycle_pnl(bs,2.5)
            if cyc and cyc.get('confirm'): confirm.add(c)
        except Exception: pass
        q={'current':st['close'],'high':st['high'],'vol':st['vol']}
        try:
            ok,_=defensive_t_reduce_quote(q,prev,wave_op='t_only')
            if ok: quote.add(c)
        except Exception: pass
        hi[c]=st['high']; hiprev[c]=max(p['high'] for p in prev)
    sig[d]={'confirm':confirm,'quote':quote,'hi':hi,'hiprev':hiprev}
def indexset(d,near,lag):
    s=set()
    sd=swdaily.get(0) or {}  # placeholder not used directly
    for c in PROX:
        if c not in sig[d]['hi']: continue
        sdd=swdaily.get(c) or {}; iw=sdd.get(d)
        ip=[sdd.get(k) for k in sorted(sdd) if k<d and sdd.get(k)][-5:]
        ip=[x for x in ip if x is not None]; ipv=max(ip) if ip else 0
        if iw and ipv and iw>=ipv*near and sig[d]['hi'][c]<sig[d]['hiprev'][c]*lag: s.add(c)
    return s
def report(near,lag,label):
    comp=set(); idx_days=[]
    for d in DAYS:
        iset=indexset(d,near,lag)
        if iset: idx_days.append(d)
        comp|=iset|sig[d]['confirm']|sig[d]['quote']
    compdays={d for d in DAYS if (sig[d]['confirm'] or sig[d]['quote'] or indexset(d,near,lag))}
    idx_sell_days={d for d in idx_days if d in WOLF_SELL}
    comp_sell={d for d in compdays if d in WOLF_SELL}
    print('[%s] near=%.3f lag=%.3f' % (label,near,lag))
    print('  index 触发日=%d (%s)  index precision=%d/%d' % (len(idx_days), ','.join(idx_days), len(idx_sell_days), len(idx_days)))
    print('  复合(确认∪quote∪index) recall=%d/4 prec=%d/%d' % (len(comp_sell), len(comp_sell), len(compdays)))
report(0.998,0.998,'基线')
report(0.998,0.98,'收紧')
