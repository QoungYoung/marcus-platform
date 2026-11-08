#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_cluster_agent_review.py — 让 agent 给全市场候选簇命名/判 new-existing。

输入 data/theme_cluster_stability.json（0.65）
输出 data/theme_cluster_agent_review.json
只读证据；不允许 agent 发明概念/成员。
"""
from __future__ import annotations
import argparse, json, os, re, sys
import yaml
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def extract(text):
    t=(text or '').strip()
    if t.startswith('```'):
        t=re.sub(r'^```[a-zA-Z]*\s*','',t); t=re.sub(r'\s*```$','',t)
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
    st=json.load(open(os.path.join(ROOT,'data','theme_cluster_stability.json'),encoding='utf-8'))
    clusters=(st.get('stability') or {}).get('0.65',{}).get('top') or []
    try:
        sys.path.insert(0,os.path.join(ROOT,'backend'))
        from app.services.theme_universe_store import load_theme_config
        cfg=load_theme_config() or {}
    except Exception:
        cfg={}
    if not cfg:
        cfg=yaml.safe_load(open(os.path.join(ROOT,'config','mainline','generated','universe_theme.yaml'),encoding='utf-8'))
    known=[{'id':t.get('id'),'name':t.get('name')} for t in (cfg.get('themes') or [])+(cfg.get('satellite_themes') or [])]
    payload=[]
    for i,c in enumerate(clusters[:10],1):
        payload.append({'cluster_id':'c%d'%i,'concepts':c.get('concepts')[:12],'members':c.get('n_cluster'),
                        'limit_up':c.get('limit_up'),'breadth':c.get('breadth'),'leader':c.get('leader')})
    prompt='''你是题材分类器。只输出一个 JSON 对象，不要 markdown、不要解释。
给定候选簇和现有题材，判断每个簇：
- name_zh：中文题材名，必须能从 concepts 概括，不能发明概念
- decision：new 或 existing 或 review
- mapped_theme_id：decision=existing 时必须从 known_themes 的 id 中选；否则 null
- confidence：0-1
- reasons：只能引用输入中的 concepts/members/limit_up/breadth/leader
- counter_evidence：不确定/可能不是题材的理由
已知题材：%s
候选簇：%s
输出 schema：
{"clusters":[{"cluster_id":"c1","name_zh":"","decision":"new|existing|review","mapped_theme_id":null,"confidence":0.0,"reasons":[""],"counter_evidence":[""]}]}
''' % (json.dumps(known,ensure_ascii=False),json.dumps(payload,ensure_ascii=False))
    import requests
    url=os.getenv('WOLF_V3_AGENT_URL') or os.getenv('PI_SERVER_URL') or 'http://dsh:3001/chat'
    try:
        r=requests.post(url,json={'message':prompt,'session_id':'theme_cluster_review_%s'%a.date},headers={'Content-Type':'application/json'},timeout=a.timeout,verify=False)
        r.raise_for_status(); raw=r.json().get('reply','')
    except Exception as e:
        print(json.dumps({'ok':False,'error':'agent_call_failed','detail':str(e)[:200]},ensure_ascii=False)); return 2
    obj=extract(raw)
    if not obj:
        print(json.dumps({'ok':False,'error':'agent_bad_json','raw':raw[:500]},ensure_ascii=False)); return 3
    ids={x['id'] for x in known}; out=[]
    for x in obj.get('clusters') or []:
        d=str(x.get('decision') or 'review')
        mid=x.get('mapped_theme_id') if d=='existing' else None
        if d=='existing' and mid not in ids: d='review'; mid=None
        out.append({'cluster_id':x.get('cluster_id'),'name_zh':str(x.get('name_zh') or '')[:40],
                    'decision':d,'mapped_theme_id':mid,'confidence':x.get('confidence'),
                    'reasons':x.get('reasons') or [],'counter_evidence':x.get('counter_evidence') or []})
    res={'ok':True,'date':a.date,'agent_url':url,'clusters':out,'raw':raw[:4000]}
    p=os.path.join(ROOT,'data','theme_cluster_agent_review.json')
    json.dump(res,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    try:
        from theme_candidate_store import save
        save(a.date,'cluster_agent',res)
    except Exception as e: print('[pg] cluster_agent save fail',str(e)[:100])
    print(json.dumps(res,ensure_ascii=False,indent=1)[:5000]); print('written',p)
    return 0
if __name__=='__main__': raise SystemExit(main())
