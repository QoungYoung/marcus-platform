# -*- coding: utf-8 -*-
import os
import json
import requests, urllib3
urllib3.disable_warnings()
PM='https://pcd.mobcvb.cn/tushare/pro'; PK=(os.getenv('PROMAX_API_KEY') or os.getenv('PROMAX_KEY') or '').strip()
def sw_high(idx,s,e):
    r=requests.get(f'{PM}/sw_daily',params={'ts_code':idx,'start_date':s,'end_date':e},headers={'X-API-Key':PK},verify=False,timeout=30)
    dd=r.json().get('data') or {}; it=dd.get('items') or []; f=dd.get('fields') or []
    fm={f[i]:i for i in range(len(f))}
    return {str(x[fm['trade_date']]).replace('-',''):float(x[fm['high']]) for x in it} if fm and it else {}
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
for c,idx in [('688981','801080.SI'),('002165','801030.SI')]:
    dc=load(c); d='20260827'; bs=dc.get(d); st=dstat(bs)
    prev=[dstat(dc[k]) for k in sorted(dc) if k<d and dc[k]][-5:]
    sym_hi=st['high']; sym_hi_prev=max(p['high'] for p in prev)
    sd=sw_high(idx,'20260818','20260903'); iw=sd.get(d)
    ip=[sd.get(k) for k in sorted(sd) if k<d and sd.get(k)][-5:]
    ip=[x for x in ip if x is not None]; ipv=max(ip) if ip else 0
    print('%s d=%s sym_hi=%.3f sym_prev=%.3f sym_lag=%s | idx_now=%.3f idx_prev=%.3f near=%s' % (c,d,sym_hi,sym_hi_prev, sym_hi<sym_hi_prev*0.998, iw,ipv,iw>=ipv*0.998))
    for dt in [s for s in sorted(dc) if s<d][-5:]:
        print('      prevday %s high=%.3f' % (dt, dstat(dc[dt])['high']))
