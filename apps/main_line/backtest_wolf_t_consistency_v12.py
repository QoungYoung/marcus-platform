# -*- coding: utf-8 -*-
"""backtest_wolf_t_consistency_v12.py — 按申万一级行业指数(sw_daily日线近高)判 defensive"""
import requests, urllib3, json
urllib3.disable_warnings()
DH='http://datahubco.com/app-api/openapi/v1/tushare'; DK='dba548a206a453c197f9175189b757374fa6db9554bb29e69efea127'
PM='https://pcd.mobcvb.cn/tushare/pro'; PK='tsr_1FjRkziz3M7m0aLcTk0ZgnK03__xO3EYq0ZdwQqdwSE'
def sw_l1(sym):
    r=requests.get(f'{DH}/index_member_all',params={'ts_code':sym},headers={'X-API-Key':DK},timeout=40)
    d=r.json(); dd=d.get('data') or {}; items=dd.get('items') or []; fields=dd.get('fields') or []
    if not items or not fields: return None
    return items[0][fields.index('l1_code')] if 'l1_code' in fields else None
def sw_high(idx, s, e):
    r=requests.get(f'{PM}/sw_daily',params={'ts_code':idx,'start_date':s,'end_date':e},headers={'X-API-Key':PK},verify=False,timeout=30)
    d=r.json(); dd=d.get('data') or {}; items=dd.get('items') or []; fields=dd.get('fields') or []
    if not items or not fields: return {}
    fmap={f:i for i,f in enumerate(fields)}
    return {str(it[fmap['trade_date']]).replace('-',''):float(it[fmap['high']]) for it in items}
def daily(code):
    import os
    out={}
    for root in ['recent_sync','stock_5m_bt']:
        p='/app/data/'+root+'/'+code+'.json'
        try: d=json.load(open(p,encoding='utf-8'))
        except: continue
        for k,v in d.items():
            bs=sorted(v,key=lambda x:str(x.get('time') or x.get('trade_time')))
            if bs: out.setdefault(k,bs)
    return out
DAYS=['20260820','20260821','20260824','20260825','20260826','20260827','20260828','20260831','20260901','20260902','20260903']
PROX={'603259':'SW','301018':'SW','688981':'SW','002945':'SW','002165':'SW'}
swmap={}
for c in PROX:
    code=c+'.SH' if c[0]=='6' else (c+'.SZ')
    swmap[c]=sw_l1(code)
    print(c,'-> SW l1=',swmap[c])
swdaily={}
for c,l1 in swmap.items():
    if l1: swdaily[c]=sw_high(l1,'20260818','20260903')
print()
print('%-8s %-40s' % ('date','SW行业级defensive(行业近高且个股未跟)'))
for d in DAYS:
    hits=[]
    detail={}
    for c in PROX:
        dc=daily(c); bs=dc.get(d)
        if not bs: continue
        symhi=max(float(b['high']) for b in bs)
        # 前5日 flattened bars
        prev=[b for k in sorted(dc) if k<d and dc[k] for b in dc[k]][-50:]
        if not prev: continue
        symprev=max(float(b['high']) for b in prev)
        sd=swdaily.get(c) or {}
        idxnow=sd.get(d)
        iprev=[sd.get(k) for k in sorted(sd) if k<d and sd.get(k)][-5:]
        iprev=[x for x in iprev if x is not None]
        idxprev=max(iprev) if iprev else 0
        near_idx = bool(idxnow and idxprev and idxnow>=idxprev*0.998)
        lag_sym = bool(symprev and symhi < symprev*0.998)
        if near_idx and lag_sym: hits.append(c+'SW'+str(swmap[c]))
        detail[c]=(symhi,symprev,idxnow,idxprev,round(symhi/symprev-1,4) if symprev else None,round(idxnow/idxprev-1,4) if idxprev else None)
    print('%-8s %-40s' % (d, (';'.join(hits) if hits else '-')))
    if d in ('20260825','20260827','20260902'):
        for c,v in detail.items():
            print('      %-6s SYM %.3f/%.3f(%.2f%%) IDX %.3f/%.3f(%.2f%%)' % (c,v[0],v[1],(v[4]*100 if v[4] else 0),v[2],v[3],(v[5]*100 if v[5] else 0)))
print()
print('预期 08-27: 医药(801150)/电子(801080)/非银(801790) 行业近高而个股未跟 => 触发index级defensive')
