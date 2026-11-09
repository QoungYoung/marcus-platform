#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_intraday_live.py — 用 promax/datahub relay 的 rt_k 生成盘中 A 数据 + 白名单候选。

输出：
  data/asof/<date>_a_intraday.json  (source_intraday + candidate_meta)
  data/asof/<date>_b_candidates.json (白名单候选，含 role_hint)
"""
from __future__ import annotations
import argparse,json,os,sys,time
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,os.path.join(ROOT,'core'))
from tushare_relay import relay_query

ALLOWED_IDS=['space','satellite','humanoid_robot','robot_actuator','ai_app','medical_device','innovative_drug']
SRC_IDS=['robot_actuator','humanoid_robot']

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--date',required=True);ap.add_argument('--max-members',type=int,default=30);a=ap.parse_args();day=a.date
 import psycopg2
 c=psycopg2.connect(os.environ['DATABASE_URL'],connect_timeout=5);cur=c.cursor()
 ids=list(dict.fromkeys(SRC_IDS+ALLOWED_IDS))
 cur.execute("select theme_id,symbol from theme_membership where theme_id=any(%s) and as_of_date=(select max(as_of_date) from theme_membership where theme_id=any(%s))",(ids,ids))
 by={}
 for tid,sym in cur.fetchall(): by.setdefault(tid,set()).add(sym)
 c.close()
 # latest facts for names/roles/source history
 facts={}; latest=None
 try:
  sys.path.insert(0,os.path.join(ROOT,'jobs'))
  from theme_sleeve_store import latest_date,load
  latest=latest_date('facts'); facts=load(latest,'facts') or {} if latest else {}
 except Exception as e: print('facts skip',str(e)[:80],flush=True)
 tmeta={t.get('theme_id'):t for t in facts.get('themes') or []}
 # source_history：优先继承最近一个 a_facts；否则用 theme_b4_history 兜底
 src_hist={}
 try:
  _asof=os.path.join(ROOT,'data','asof')
  _files=sorted([x for x in os.listdir(_asof) if x.endswith('_a_facts.json') and x[:8]<day]) if os.path.isdir(_asof) else []
  for _f in reversed(_files):
   _x=json.load(open(os.path.join(_asof,_f),encoding='utf-8'))
   if _x.get('source_history'):
    src_hist=_x['source_history']; break
 except Exception: pass
 if not src_hist:
  try:
   _b4=json.load(open(os.path.join(ROOT,'data','theme_b4_history.json'),encoding='utf-8'))
   for _name in ('人形机器人','减速器','机器人执行器'):
    _tid={'人形机器人':'humanoid_robot','减速器':'robot_actuator','机器人执行器':'robot_actuator'}.get(_name)
    if _tid and _b4.get(_tid):
     src_hist[_name]=[{'date':z.get('date'),'n':int(z.get('u') or 0)+int(z.get('z') or 0)+1,
                       'breadth':round(int(z.get('u') or 0)/max(1,int(z.get('u') or 0)+int(z.get('z') or 0)+1),3),
                       'limit':int(z.get('u') or 0),'avg':0.0} for z in _b4[_tid][-7:]]
  except Exception: pass
 # 过滤：源题材全量；候选题材只保留 role_hint + 市值前 N，避免 1100 只串行
 try:
  _mv={}
  with psycopg2.connect(os.environ['DATABASE_URL'],connect_timeout=3) as _c:
   with _c.cursor() as _cur:
    _cur.execute("select ts_code,total_mv from mkt_bars_daily where trade_date=(select max(trade_date) from mkt_bars_daily where trade_date<=%s)",(day,))
    _mv={str(r[0]):float(r[1] or 0) for r in _cur.fetchall()}
  for _tid in list(by):
   if _tid in SRC_IDS: continue
   _t=tmeta.get(_tid) or {}; _rd=_t.get('role_detail') or {}; _roles=_t.get('roles') or {}
   _keep=set(x for x in [_roles.get('leader')]+list(_roles.get('wind') or [])+list(_roles.get('mid') or []) if x)
   _keep |= set(x for x in [((_rd.get('leader') or {}).get('symbol'))] if x)
   _rest=sorted(by[_tid]-_keep,key=lambda x:-_mv.get(x,0))
   by[_tid]=_keep|set(_rest[:max(0,a.max_members-len(_keep))])
 except Exception as e:
  print('member filter skip',str(e)[:80],flush=True)
 srcset=set().union(*[by.get(x,set()) for x in SRC_IDS]) if any(by.get(x) for x in SRC_IDS) else set()
 qcache={}
 def q(sym):
  if sym in qcache: return qcache[sym]
  try:
   x=relay_query('rt_k',ts_code=sym,limit=1)
   if x is None or len(x)==0: qcache[sym]=None; return None
   r=x.iloc[0]; pc=float(r['pre_close'] or 0); cl=float(r['close'] or 0)
   qcache[sym]={'pct':round((cl/pc-1)*100,2)} if pc>0 else None
  except Exception: qcache[sym]=None
  return qcache[sym]
 out={}
 for tid in ids:
  mem=by.get(tid) or set(); rows=[]
  for sym in sorted(mem):
   z=q(sym)
   if not z: continue
   rows.append({'symbol':sym,'pct':z['pct'],'limit':z['pct']>=9.7})
  n=len(rows); up=sum(1 for r in rows if r['pct']>0); lu=sum(1 for r in rows if r['limit'])
  out[tid]={'n':n,'breadth':round(up/n,3) if n else 0,'limit_up':lu,'ret1':round(sum(r['pct'] for r in rows)/n,2) if n else 0,'members':rows}
  print(tid,n,out[tid]['breadth'],lu,out[tid]['ret1'],flush=True)
 # A candidate_meta / B candidates
 cm={}; bc=[]
 for tid in ALLOWED_IDS:
  if tid not in out: continue
  t=tmeta.get(tid) or {}; name=t.get('name') or tid; o=out[tid]
  inter=len(srcset & (by.get(tid) or set())) if tid not in SRC_IDS else len(srcset)
  purity=round(inter/max(1,len(by.get(tid) or set())),3)
  meta={'first_day_absolute': bool(o['limit_up']>=3 and o['breadth']>=0.6),
        'prior_launch_days':[], 'limit_up':o['limit_up'],'breadth':o['breadth'],
        'ret1':o['ret1'],'overlap':inter,'overlap_ratio':purity,'target_purity':purity}
  cm[name]=meta
  rd=t.get('role_detail') or {}; roles=t.get('roles') or {}
  bc.append({'concept':name,'theme_id':tid,'n':o['n'],'overlap':inter,
             'target_purity':purity,'limit_up':o['limit_up'],'breadth':o['breadth'],
             'ret1':o['ret1']/100 if o['ret1'] else 0,**meta,
             'role_hint':{'leader':roles.get('leader'),'wind':roles.get('wind') or [],'mid':roles.get('mid') or []},
             'leader':(rd.get('leader') or {}).get('symbol') or roles.get('leader')})
 os.makedirs(os.path.join(ROOT,'data/asof'),exist_ok=True)
 a_payload={'date':day,'source':'机器人链','source_history':src_hist,'source_intraday':{k:out[k] for k in SRC_IDS if k in out},'candidate_meta':cm}
 b_payload={'date':day,'source':'机器人链','candidates':bc,'candidate_meta':cm}
 # 不落 data/asof；只入 PG，避免同一天多槽覆盖历史 JSON
 try:
  sys.path.insert(0,os.path.join(ROOT,'jobs'))
  from theme_candidate_store import save as _save
  _save(day,'a_intraday',a_payload); _save(day,'b_candidates',b_payload)
  print('PG saved',day,flush=True)
 except Exception as e:
  print('PG save skip:',str(e)[:100],flush=True)
 print('written',day,'candidates',len(bc),'facts',latest)
if __name__=='__main__': main()
