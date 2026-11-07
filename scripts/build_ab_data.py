#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""build_ab_data.py — 为指定日期生成 A facts + B 候选（日线口径）。"""
from __future__ import annotations
import argparse,json,os,sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,os.path.join(ROOT,'core'))
from tushare_relay import relay_query
SRC=['人形机器人','减速器','机器人执行器']
PREV=['20261016','20261019','20261020','20261021','20261022','20261023','20261026','20261027','20261028','20261029','20261030','20261102','20261103','20261104','20261105']
def allowed():
 import yaml
 try:
  c=yaml.safe_load(open(os.path.join(ROOT,'config/mainline/theme_allowed.yaml'),encoding='utf-8')) or {}
  return set(c.get('allowed') or [])
 except Exception: return set()

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--date',required=True);a=ap.parse_args();day=a.date
 s=json.load(open(os.path.join(ROOT,'data/asof/20261028_snapshot.json'),encoding='utf-8'))['concepts']
 names=sorted({x for v in s.values() for x in v})
 dates=[d for d in PREV if d<=day][-7:]
 daily={}; lim={}
 for d in dates:
  x=relay_query('daily',trade_date=d,limit=6000); daily[d]={str(r.ts_code):float(r.pct_chg or 0) for _,r in x.iterrows()}
  y=relay_query('limit_list_d',trade_date=d,limit=6000); lim[d]={str(r.ts_code):r for _,r in y.iterrows()}
 def mem(n): return [c for c,v in s.items() if n in v]
 def hist(t):
  out=[]
  for d in dates:
   vals=[daily[d].get(c) for c in t if c in daily[d]]; lc=sum(1 for c in t if c in lim[d] and str(lim[d][c].get('limit'))=='U')
   out.append({'date':d,'n':len(vals),'breadth':round(sum(1 for v in vals if v>0)/len(vals),3) if vals else 0,'limit':lc,'avg':round(sum(vals)/len(vals),2) if vals else 0})
  return out
 src={n:hist(mem(n)) for n in SRC}
 a_payload={'date':day,'source':'机器人链','source_history':src,'candidate_meta':{}}
 json.dump(a_payload,open(os.path.join(ROOT,'data/asof',f'{day}_a_facts.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=1)
 srcset=set().union(*[set(mem(n)) for n in SRC]); rows=[]
 _allow=allowed()
 _roles={}
 try:
  _roles=json.load(open(os.path.join(ROOT,'data/asof',f'{day}_roles.json'),encoding='utf-8'))
 except Exception: pass
 for n in names:
  if _allow and n not in _allow: continue
  t=mem(n)
  if len(t)<5: continue
  inter=srcset&set(t); tp=len(inter)/len(t) if t else 0
  h=hist(t); today=h[-1]; prior=h[:-1]
  rows.append({'concept':n,'n':len(t),'overlap':len(inter),'target_purity':round(tp,3),'source_coverage':round(len(inter)/len(srcset),3),
   'limit_up':today['limit'],'breadth':today['breadth'],'ret1':today['avg']/100 if today['avg'] else 0,'history_10d':h,
   'prior_breadth_5d':round(sum(x['breadth'] for x in prior)/len(prior),3),'prior_limit_5d':round(sum(x['limit'] for x in prior)/len(prior),2),
   'breadth_jump':round(today['breadth']-sum(x['breadth'] for x in prior)/len(prior),3),
   'first_day_absolute':sum(x['limit'] for x in prior if x['limit']>=3 and (x.get('breadth') or 0)>=0.6)==0,
   'role_hint':(_roles.get('roles') if n==_roles.get('target') else None),
   'leader':(((_roles.get('roles') or {}).get('leader')) if n==_roles.get('target') else None)})
 rows.sort(key=lambda x:(-x['overlap'],-x['target_purity']))
 b_payload={'date':day,'source':'机器人链','candidates':rows[:30],'candidate_meta':{x['concept']:{k:x.get(k) for k in ('first_day_absolute','prior_launch_days','limit_up','breadth','overlap','overlap_ratio','target_purity')} for x in rows[:30]}}
 a_payload['candidate_meta']=b_payload['candidate_meta']
 json.dump(b_payload,open(os.path.join(ROOT,'data/asof',f'{day}_b_candidates.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=1)
 json.dump(a_payload,open(os.path.join(ROOT,'data/asof',f'{day}_a_facts.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=1)
 try:
  sys.path.insert(0,os.path.join(ROOT,'jobs'))
  from theme_candidate_store import save as _save
  _save(day,'a_facts',a_payload); _save(day,'b_candidates',b_payload)
 except Exception as e:
  print('PG save skip:',str(e)[:100])
 print(day,'A/B written',len(rows))
if __name__=='__main__': main()
