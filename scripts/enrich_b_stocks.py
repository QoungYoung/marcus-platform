#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""enrich_b_stocks.py — 给 B 候选补股票池（含核心/配套提示）。"""
from __future__ import annotations
import argparse,json,os,sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,os.path.join(ROOT,'core'))
from tushare_relay import relay_query
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--date',required=True);a=ap.parse_args();day=a.date
 s=json.load(open(os.path.join(ROOT,'data/asof/20261028_snapshot.json'),encoding='utf-8'))['concepts']
 dpath=os.path.join(ROOT,'data/asof',f'{day}_b_candidates.json'); b=json.load(open(dpath,encoding='utf-8'))
 daily=relay_query('daily',trade_date=day,limit=6000); q={str(r.ts_code):r for _,r in daily.iterrows()}
 lim=relay_query('limit_list_d',trade_date=day,limit=6000); l={str(r.ts_code):r for _,r in lim.iterrows()}
 # 事实角色
 roles={}
 fp=os.path.join(ROOT,'data','mainline',day,'theme_sleeve_facts.json')
 if os.path.exists(fp):
  for t in json.load(open(fp)).get('themes') or []:
   roles[t.get('theme_id')]={k:(t.get('roles') or {}).get(k) for k in ('leader','wind','mid')}
 for c in b.get('candidates') or []:
  mem=[x for x,v in s.items() if c['concept'] in v]
  stocks=[]
  for x in mem:
   r=q.get(x)
   if r is None: continue
   lr=l.get(x)
   if lr is None: lr={}
   def sv(v):
    try:
     if hasattr(v,'iloc'): return v.iloc[0]
    except Exception: pass
    return v
   stocks.append({'symbol':x,'pct':round(float(sv(r.pct_chg) or 0),2),'amount':float(sv(r.amount) or 0),
                  'limit':str(sv(lr.get('limit')) or ''),'limit_times':sv(lr.get('limit_times')),'first_time':str(sv(lr.get('first_time')) or '')})
  stocks.sort(key=lambda z:(z['limit']=='U',z['amount']),reverse=True)
  c['stocks']=stocks[:20]
  c['role_hint']=roles.get(c['concept'])
 json.dump(b,open(os.path.join(ROOT,'data/asof',f'{day}_b_candidates_stocks.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=1)
 print(day,'stocks added',len(b.get('candidates') or []))
if __name__=='__main__': main()
