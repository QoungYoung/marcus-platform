# -*- coding: utf-8 -*-
"""backtest_wolf_t_consistency_v13.py — 综合卖出信号(确认制T出+个股防御+申万行业防御)近两周一致率"""
import sys, os, json
sys.path.insert(0, '/app')
from app.services.wolf_t_rules import defensive_t_reduce_quote, t_cycle_pnl
import requests, urllib3
urllib3.disable_warnings()
DH='http://datahubco.com/app-api/openapi/v1/tushare'; DK='dba548a206a453c197f9175189b757374fa6db9554bb29e69efea127'
PM='https://pcd.mobcvb.cn/tushare/pro'; PK='tsr_1FjRkziz3M7m0aLcTk0ZgnK03__xO3EYq0ZdwQqdwSE'
def sw_l1(sym):
    r=requests.get(f'{DH}/index_member_all',params={'ts_code':sym},headers={'X-API-Key':DK},timeout=40)
    d=r.json(); dd=d.get('data') or {}; it=dd.get('items') or []; f=dd.get('fields') or []
    if not it or not f: return None
    fm={f[i]:i for i in range(len(f))}
    return it[0][fm['l1_code']] if 'l1_code' in fm else None
def sw_high(idx,s,e):
    r=requests.get(f'{PM}/sw_daily',params={'ts_code':idx,'start_date':s,'end_date':e},headers={'X-API-Key':PK},verify=False,timeout=30)
    dd=r.json().get('data') or {}; it=dd.get('items') or []; f=dd.get('fields') or []
    fm={f[i]:i for i in range(len(f))}
    return {str(x[fm['trade_date']]).replace('-',''):float(x[fm['high']]) for x in it} if fm and it else {}
def load(code):
    import os as _o
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
    hs=[float(b['high']) for b in bs]; ls=[float(b['low']) for b in bs]
    return {'close':float(bs[-1]['close']),'high':max(hs),'low':min(ls),
            'vol':sum(float(b.get('vol') or 0) for b in bs)}
WOLF_SELL={'20260821','20260827','20260901','20260902'}
DAYS=['20260820','20260821','20260824','20260825','20260826','20260827','20260828','20260831','20260901','20260902','20260903']
PROX=['603259','301018','688981','002945','002165']
swmap={c:sw_l1(c+'.SH' if c[0]=='6' else c+'.SZ') for c in PROX}
swdaily={c:sw_high(l1,'20260818','20260903') for c,l1 in swmap.items() if l1}
# per-day sell signals: confirm / quote-defensive / index-defensive
# index per date: near-high + sym-lag  (replicating defensive_t_reduce_sw with per-date index)
def idx_ok(c, d, sym_hi_now, sym_hi_prev):
    sd=swdaily.get(c) or {}
    iw=sd.get(d)
    ip=[sd.get(k) for k in sorted(sd) if k<d and sd.get(k)][-5:]
    ip=[x for x in ip if x is not None]
    ipv=max(ip) if ip else 0
    return bool(iw and ipv and iw>=ipv*0.998 and sym_hi_now and sym_hi_prev and sym_hi_now<sym_hi_prev*0.998)
rows={}
for d in DAYS:
    day={}
    for c in PROX:
        dc=load(c); bs=dc.get(d)
        if not bs: day[c]={'confirm':False,'quote':False,'index':False}; continue
        st=dstat(bs)
        prev=[dstat(dc[k]) for k in sorted(dc) if k<d and dc[k]][-5:]
        confirm=False
        try:
            cyc=t_cycle_pnl(bs,2.5)
            confirm=bool(cyc and cyc.get('confirm'))
        except Exception: pass
        quote=False
        if prev:
            q={'current':st['close'],'high':st['high'],'vol':st['vol']}
            try: quote,_=defensive_t_reduce_quote(q,prev,wave_op='t_only')
            except Exception: pass
        idx_prev=max(p['high'] for p in prev) if prev else 0
        idx=idx_ok(c,d,st['high'],idx_prev)
        day[c]={'confirm':confirm,'quote':quote,'index':idx}
    rows[d]=day
# aggregate
print('%-8s | %-16s | %-6s %-6s %-6s %-6s' % ('date','any_sell','confirm','quote','index','wolf_sell'))
composite_recall=0; composite_prec_n=0; wolf_days=0
for d in DAYS:
    any_sell=set(); cset=set(); qset=set(); iset=set()
    for c,fl in rows[d].items():
        if fl['confirm']: cset.add(c)
        if fl['quote']: qset.add(c)
        if fl['index']: iset.add(c)
        if fl['confirm'] or fl['quote'] or fl['index']: any_sell.add(c)
    ws = 'YES' if d in WOLF_SELL else 'no'
    if d in WOLF_SELL: wolf_days+=1
    if any_sell and d in WOLF_SELL: composite_recall+=1
    if any_sell: composite_prec_n+=1
    print('%-8s | %-16s | %-6s %-6s %-6s %-6s' % (d, (';'.join(sorted(any_sell)) or '-'), (';'.join(sorted(cset)) or '-'), (';'.join(sorted(qset)) or '-'), (';'.join(sorted(iset)) or '-'), ws))
print()
print('卖出日=4 (08-21/08-27/09-01/09-02)')
print('组合(确认∪个股防御∪行业防御) sell recall=%d/%d=%.0f%%' % (composite_recall, wolf_days, composite_recall/wolf_days*100 if wolf_days else 0))
print('组合 sell 触发日 precision=%d/%d=%.0f%%(含未卖日误报)' % (composite_recall, composite_prec_n, composite_recall/composite_prec_n*100 if composite_prec_n else 0))
