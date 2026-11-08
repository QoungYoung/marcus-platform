#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""promote_theme_candidates.py — 把 agent 确认的候选 promote 到 PG theme_definition。"""
from __future__ import annotations
import argparse, json, os, sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT,os.path.join(ROOT,'jobs'),os.path.join(ROOT,'backend'),os.path.join(ROOT,'core')):
    if p not in sys.path: sys.path.insert(0,p)
from app.services.theme_universe_store import save_themes, save_members
from stock_agent_facts import members_for


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',default='20261102'); ap.add_argument('--ids',default='media,data_elements,xinchuang')
    a=ap.parse_args()
    res=json.load(open(os.path.join(ROOT,'data','theme_candidate_resolver_20261103.json'),encoding='utf-8'))
    ag=json.load(open(os.path.join(ROOT,'data','theme_candidate_leader_agent.json'),encoding='utf-8'))
    by={x.get('theme_id'):x for x in ag.get('themes') or []}
    wanted=[x.strip() for x in a.ids.split(',') if x.strip()]
    out=[]
    for t in res.get('themes') or []:
        tid=t.get('id')
        if tid not in wanted: continue
        x=by.get(tid) or {}
        if not x.get('leader'): print('skip no leader',tid); continue
        t=dict(t); t['leader']=x['leader']; t['ladder']=x.get('ladder') or []
        t['leader_method']='agent'; t['status']='active'
        out.append(t)
    n=save_themes(out,False,status='active')
    print('promoted themes',n,[t['id'] for t in out])
    for t in out:
        srcs=list(t.get('sources') or [])+list(((t.get('resolution') or {}).get('selected')) or [])
        t2=dict(t); t2['sources']=srcs
        try: mem=set(members_for(t2,a.date))
        except Exception: mem=set()
        if mem: save_members(t['id'],mem,a.date,source='resolver'); print('members',t['id'],len(mem))
    return 0
if __name__=='__main__': raise SystemExit(main())
