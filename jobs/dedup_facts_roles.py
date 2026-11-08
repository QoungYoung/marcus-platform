#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dedup_facts_roles.py — 同一 symbol 在多个题材角色中只保留一个归属。"""
from __future__ import annotations
import argparse, json, os, sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT,os.path.join(ROOT,'jobs'),os.path.join(ROOT,'backend')):
    if p not in sys.path: sys.path.insert(0,p)
from theme_sleeve_store import load, save

RANK={'probe_candidate':4,'watch_strong':3,'watch':2,'reject':1}
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',default='20261102'); a=ap.parse_args()
    data=load(a.date,'facts')
    if not data: print('no facts'); return 2
    themes=data.get('themes') or []
    occ={}
    for t in themes:
        for role in ('leader','wind','mid'):
            val=(t.get('roles') or {}).get(role) or []
            if isinstance(val,str): val=[val]
            for s in val:
                if s: occ.setdefault(s,[]).append((t.get('theme_id'),role,RANK.get(t.get('tier'),0),bool(t.get('first_linkage'))))
    removed=0
    for s,items in occ.items():
        if len(items)<=1: continue
        # 优先：该票是 leader > first_linkage > tier > 顺序
        items=sorted(items,key=lambda x:(-(x[1]=='leader'),-x[3],-x[2]))
        keep=items[0][0]
        for tid,role,_,_ in items[1:]:
            t=next(x for x in themes if x.get('theme_id')==tid)
            r=t.get('roles') or {}
            val=r.get(role) or []
            if isinstance(val,str): val=[val]
            if s in val:
                r[role]=[x for x in val if x!=s]; t['roles']=r; removed+=1
                rd=t.get('role_detail') or {}
                if isinstance(rd.get(role),list): rd[role]=[z for z in rd[role] if z.get('symbol')!=s]
                elif isinstance(rd.get(role),dict) and rd[role].get('symbol')==s: rd[role]=None
                t['role_detail']=rd
                # 同步移除 intraday/sync
                intr=t.get('intraday') or {}; intr['roles']=[z for z in intr.get('roles') or [] if z.get('symbol')!=s]; t['intraday']=intr
                sync=(t.get('internal_linkage') or {}).get('sync') or {}
                if isinstance(sync,dict):
                    sync['roles']=[z for z in sync.get('roles') or [] if z.get('symbol')!=s]
                    rs=[z.get('r') for z in sync['roles'] if z.get('r') is not None]
                    sync['mean']=round(sum(rs)/len(rs),2) if rs else None; sync['n']=len(rs)
                    t.setdefault('internal_linkage',{})['sync']=sync
                print('dedup',s,'from',tid,role,'keep',keep)
    data['themes']=themes
    save(a.date,'facts',data)
    p=os.path.join(ROOT,'data','mainline',a.date,'theme_sleeve_facts.json')
    try: json.dump(data,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    except Exception: pass
    print('removed',removed)
    return 0
if __name__=='__main__': raise SystemExit(main())
