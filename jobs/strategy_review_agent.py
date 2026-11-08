#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""strategy_review_agent.py — 红队评审：对照狼大规范，找落地违规。"""
from __future__ import annotations
import argparse, json, os, re, sys
import yaml
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT,os.path.join(ROOT,'jobs'),os.path.join(ROOT,'backend'),os.path.join(ROOT,'core')):
    if p not in sys.path: sys.path.insert(0,p)


def extract(text):
    t=(text or '').strip()
    if t.startswith('```'): t=re.sub(r'^```[a-zA-Z]*\s*','',t); t=re.sub(r'\s*```$','',t)
    try: return json.loads(t)
    except Exception: pass
    s=t.find('{'); e=t.rfind('}')
    if s>=0 and e>s:
        try: return json.loads(t[s:e+1])
        except Exception: pass
    return None


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',default='20261102'); ap.add_argument('--timeout',type=float,default=180)
    a=ap.parse_args()
    from theme_sleeve_store import load
    facts=load(a.date,'facts') or {}; sel=load(a.date,'selected') or {}
    policy=yaml.safe_load(open(os.path.join(ROOT,'config','mainline','wolf_policy.yaml'),encoding='utf-8'))
    ev=[]
    selected={x.get('theme_id') for x in sel.get('selected') or []}
    for t in facts.get('themes') or []:
        ev.append({'theme_id':t.get('theme_id'),'name':t.get('name'),'tier':t.get('tier'),
                   'selected':t.get('theme_id') in selected,'selected_symbol':next((x.get('symbol') for x in sel.get('selected') or [] if x.get('theme_id')==t.get('theme_id')),None),
                   'b4':t.get('b4'),'overhang':t.get('overhang'),'first_linkage':t.get('first_linkage'),
                   'internal_linkage':t.get('internal_linkage'),'roles':t.get('roles'),'role_detail':t.get('role_detail'),
                   'agent_state':t.get('agent_state'),'ret1':t.get('ret1'),'breadth':t.get('breadth')})
    prompt='''你是狼大策略红队评审。只输出 JSON。对照 policy 和 unsourced_thresholds，找出当前落地与狼大原话不一致的地方。
重点：不要替代码辩护；没有狼大出处的硬阈值必须报 unsourced_rule；agent 说 buy/new_buy 却被过滤器挡掉必须报 agent_overridden；高潮仍追龙头报 climax_conflict。
严重度口径：high 只用于「已有选中/计划下单」或「agent 明确 new_buy=true 被硬门压掉」的实际违规；没有 selected、只是证据缺失/输出未落地/潜在口径问题，最高 medium。
输入：%s
输出：
{"violations":[{"rule_id":"","severity":"high|medium|low","theme_id":"","evidence":"","why":"","fix":""}],"summary":""}
''' % json.dumps({'policy':policy,'themes':ev},ensure_ascii=False)
    import requests
    url=os.getenv('WOLF_V3_AGENT_URL') or os.getenv('PI_SERVER_URL') or 'http://dsh:3001/chat'
    try:
        r=requests.post(url,json={'message':prompt,'session_id':'strategy_review_%s'%a.date},headers={'Content-Type':'application/json'},timeout=a.timeout,verify=False)
        r.raise_for_status(); raw=r.json().get('reply','')
    except Exception as e:
        print(json.dumps({'ok':False,'error':'agent_call_failed','detail':str(e)[:200]},ensure_ascii=False)); return 2
    obj=extract(raw)
    if not obj:
        print(json.dumps({'ok':False,'error':'bad_json','raw':raw[:500]},ensure_ascii=False)); return 3
    res={'ok':True,'date':a.date,'violations':obj.get('violations') or [],'summary':obj.get('summary',''),'raw':raw[:4000]}
    p=os.path.join(ROOT,'data',f'strategy_review_{a.date}.json')
    json.dump(res,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    print(json.dumps(res,ensure_ascii=False,indent=1)[:6000]); print('written',p)
    return 0
if __name__=='__main__': raise SystemExit(main())
