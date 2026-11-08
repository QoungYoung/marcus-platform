#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_candidate_dedup.py — 候选题材成员/龙头归属去重。"""
from __future__ import annotations
import json, os, sys
from collections import defaultdict
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT,os.path.join(ROOT,'jobs'),os.path.join(ROOT,'backend'),os.path.join(ROOT,'core')):
    if p not in sys.path: sys.path.insert(0,p)
from stock_agent_facts import members_for

def ov(a,b): return len(a&b)/min(len(a),len(b)) if a and b else 0

def main():
    res=json.load(open(os.path.join(ROOT,'data','theme_candidate_resolver_20261103.json'),encoding='utf-8'))
    agent=json.load(open(os.path.join(ROOT,'data','theme_candidate_leader_agent.json'),encoding='utf-8'))
    ag={x['theme_id']:x for x in agent.get('themes') or []}
    ts={t['id']:t for t in res.get('themes') or []}
    mem={}
    for tid,t in ts.items():
        srcs=list(t.get('sources') or [])+list(((t.get('resolution') or {}).get('selected')) or [])
        t2=dict(t); t2['sources']=srcs
        try: mem[tid]=set(members_for(t2,'20261102'))
        except Exception: mem[tid]=set()
    ids=list(ts)
    rel=[]
    for i,a in enumerate(ids):
        for b in ids[i+1:]:
            x=len(mem.get(a,set())&mem.get(b,set())); o=ov(mem.get(a,set()),mem.get(b,set())); j=x/len(mem.get(a,set())|mem.get(b,set())) if (mem.get(a,set())|mem.get(b,set())) else 0
            if o>=0.5 and min(len(mem[a]),len(mem[b]))/max(len(mem[a]),len(mem[b]))<=0.7:
                rel.append({'a':a,'b':b,'relation':'child','overlap':round(o,3),'jaccard':round(j,3),'shared':x,'parent':b if len(mem[b])>len(mem[a]) else a,'child':a if len(mem[b])>len(mem[a]) else b})
            elif o>=0.6:
                rel.append({'a':a,'b':b,'relation':'merge','overlap':round(o,3),'jaccard':round(j,3),'shared':x})
            else:
                rel.append({'a':a,'b':b,'relation':'sibling','overlap':round(o,3),'jaccard':round(j,3),'shared':x})
    # 同一代码出现在多个题材的 leader/ladder -> 归到置信度最高题材，其余标冲突
    owner=defaultdict(list)
    for tid,x in ag.items():
        if x.get('leader'): owner[x['leader']['code']].append((tid,'leader'))
        for z in x.get('ladder') or []: owner[z['code']].append((tid,'ladder'))
    conflicts=[]
    for code,claims in owner.items():
        if len(claims)>1:
            claims_sorted=sorted(claims,key=lambda c:-(ag.get(c[0],{}).get('confidence') or 0))
            primary=claims_sorted[0][0]
            for tid,role in claims_sorted[1:]: conflicts.append({'code':code,'primary':primary,'secondary':tid,'role':role})
    # 输出每个题材 leader/ladder，附 primary 标记（不强行改选）
    out=[]
    for tid,x in ag.items():
        out.append({'theme_id':tid,'leader':x.get('leader'),'ladder':x.get('ladder'),
                    'leader_owner': next((c['primary'] for c in conflicts if c['code']==(x.get('leader') or {}).get('code')), tid),
                    'confidence':x.get('confidence'),'decision':x.get('decision')})
    p=os.path.join(ROOT,'data','theme_candidate_dedup.json')
    payload={'relations':rel,'conflicts':conflicts,'themes':out}
    json.dump(payload,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    try:
        from theme_candidate_store import save
        save('20261103','dedup',payload)
    except Exception as e: print('[pg] dedup save fail',str(e)[:100])
    print('relations')
    for r in rel: print(r)
    print('conflicts')
    for c in conflicts: print(c)
    print('written',p)
    return 0
if __name__=='__main__': raise SystemExit(main())
