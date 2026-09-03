# -*- coding: utf-8 -*-
"""backtest_wolf_t_consistency_v14.py — 收紧 SW 行业防御阈值网格, 找 recall=100% 且 precision 最高"""
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
    fm={f[i]:i for i in range(len(f))}
    out={}
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
# per-day confirm/quote (fixed) + per-symbol highs
day_signal={}
for d in DAYS:
    confirm=set(); quote=set(); hi={}; hiprev={}
    for c in PROX:
        dc=load(c); bs=dc.get(d)
        if not bs: continue
        st=dstat(bs)
        prev=[dstat(dc[k]) for k in sorted(dc) if k<d and dc[k]][-5:]
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
    day_signal[d]={'confirm':confirm,'quote':quote,'hi':hi,'hiprev':hiprev}
def idx_ok(c,d,hi,hiprev,near_th,lag_th):
    sd=swdaily.get(c) or {}
    iw=sd.get(d)
    ip=[sd.get(k) for k in sorted(sd) if k<d and sd.get(k)][-5:]
    ip=[x for x in ip if x is not None]
    ipv=max(ip) if ip else 0
    return bool(iw and ipv and iw>=ipv*near_th and hi and hiprev and hi<hiprev*lag_th)
# sweep
combos=[]
for near_th in [0.998,0.995,0.99,0.985,0.98]:
    for lag_th in [0.998,0.99,0.985,0.98,0.97]:
        recall=0; prec_n=0; idx_days=[]
        for d in DAYS:
            anyidx=set()
            for c in PROX:
                if c in day_signal[d]['hi'] and idx_ok(c,d,day_signal[d]['hi'][c],day_signal[d]['hiprev'][c],near_th,lag_th):
                    anyidx.add(c)
            if anyidx:
                prec_n+=1; idx_days.append(d)
            if (d in WOLF_SELL) and (anyidx or day_signal[d]['confirm'] or day_signal[d]['quote']):
                recall+=1
        combos.append((near_th,lag_th,recall,prec_n,idx_days))
combos.sort(key=lambda x:(-x[2], -x[3]))
print('%-8s %-8s | %-6s %-6s %s' % ('near','lag','recall','prec','index_days'))
for near_th,lag_th,rec,prec,idx_days in combos:
    if rec==4:
        print('%-8.3f %-8.3f | %-6d %-6s %s' % (near_th,lag_th,rec,str(prec)+'/10', ','.join(idx_days)))
print()
print('当前基线(0.998/0.998): recall=100% prec=40%')
print('--> 目标: 在 recall 仍=4/4 的前提下提升 precision (减少 08-24/28/31/09-03 误报)')
