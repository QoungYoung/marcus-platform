#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多题材每15分钟快照：一次抓并集分钟K。"""
from __future__ import annotations
import argparse,json,os,sys
from concurrent.futures import ThreadPoolExecutor,as_completed
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,os.path.join(ROOT,'core'))
from tushare_relay import relay_query

def lim(c):
 c=str(c).upper()
 if c.startswith(('300','301','688','689')): return 20.0
 if c.startswith(('8','4','92')): return 30.0
 return 10.0

def fetch(c,day):
 try:
  x=relay_query('stk_mins',ts_code=c,freq='1min',start_date=f'{day[:4]}-{day[4:6]}-{day[6:]} 09:30:00',end_date=f'{day[:4]}-{day[4:6]}-{day[6:]} 15:00:00',limit=1000)
  return c,(x.sort_values('trade_time') if x is not None and len(x) else None)
 except Exception: return c,None

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--date',required=True);ap.add_argument('--concepts',required=True);a=ap.parse_args();day=a.date
 concepts=[x.strip() for x in a.concepts.split(',') if x.strip()]
 snap=json.load(open(os.path.join(ROOT,'data/asof',f'{day}_snapshot.json'),encoding='utf-8'))['concepts']
 sets={n:{c for c,v in snap.items() if n in v} for n in concepts}; allm=set().union(*sets.values())
 daily=relay_query('daily',trade_date=day,limit=6000); pre={str(r.ts_code):float(r.pre_close) for _,r in daily.iterrows()}
 data={}
 with ThreadPoolExecutor(max_workers=10) as ex:
  for f in as_completed([ex.submit(fetch,c,day) for c in allm]):
   c,x=f.result()
   if x is not None: data[c]=x
 times=['0945','1000','1015','1030','1045','1100','1115','1130','1300','1315','1330','1345','1400','1415','1430','1445','1500']
 res={n:[] for n in concepts}
 for hm in times:
  for n,mem in sets.items():
   rows=[]
   for c in mem:
    x=data.get(c); pc=pre.get(c)
    if x is None or not pc: continue
    t=f'{hm[:2]}:{hm[2:]}'; y=x[x.trade_time.astype(str).str[-5:]<=t]
    if len(y)==0: continue
    p=float(y.iloc[-1]['close']); pct=(p/pc-1)*100
    rows.append({'symbol':c,'pct':round(pct,2),'limit':pct>=lim(c)-0.3})
   nn=len(rows); up=sum(1 for r in rows if r['pct']>0); lu=sum(1 for r in rows if r['limit'])
   ld=max(rows,key=lambda r:r['pct']) if rows else None
   res[n].append({'time':hm,'n':nn,'breadth':round(up/nn,3) if nn else 0,'limit_up':lu,'ret1':round(sum(r['pct'] for r in rows)/nn,2) if nn else 0,'leader':ld})
 for n in concepts:
  print('\n==',n)
  for x in res[n][::3]: print(x['time'],'br',x['breadth'],'lu',x['limit_up'],'ret',x['ret1'],'leader',x['leader'])
 p=os.path.join(ROOT,'data/intraday',f'{day}_multi_15m.json');json.dump(res,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1);print('written',p)
if __name__=='__main__': main()
