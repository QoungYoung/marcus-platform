#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_candidate_leader_agent.py — 让 agent 在候选题材成员里选 leader/ladder。"""
from __future__ import annotations
import argparse, json, os, re, sys
from datetime import datetime, timedelta
import yaml
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT,os.path.join(ROOT,'core'),os.path.join(ROOT,'jobs'),os.path.join(ROOT,'backend')):
    if p not in sys.path: sys.path.insert(0,p)
from tushare_relay import relay_query
from stock_agent_facts import members_for, _fetch_bars_multi
from app.services.theme_dedup import dedup


def _i(v):
    try:
        f=float(v)
        return 0 if f!=f else int(f)
    except Exception:
        return 0


def _f(v):
    try:
        f=float(v)
        return 0.0 if f!=f else f
    except Exception:
        return 0.0


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
    ap=argparse.ArgumentParser(); ap.add_argument('--date',default='20261102'); ap.add_argument('--timeout',type=float,default=240)
    a=ap.parse_args()
    cand=json.load(open(os.path.join(ROOT,'data','theme_candidate_resolver_20261103.json'),encoding='utf-8'))
    df=relay_query('daily',trade_date=a.date,limit=6000)
    px={str(r.get('ts_code')):{'pct':float(r.get('pct_chg') or 0),'amount':float(r.get('amount') or 0),'close':float(r.get('close') or 0)} for _,r in df.iterrows()} if df is not None else {}
    lu=relay_query('limit_list_d',trade_date=a.date,limit=6000)
    lm={str(r.get('ts_code')):r.to_dict() for _,r in lu.iterrows()} if lu is not None else {}
    cy=relay_query('cyq_perf',trade_date=a.date,limit=6000)
    cm={str(r.get('ts_code')):r.to_dict() for _,r in cy.iterrows()} if cy is not None else {}
    try:
        lk=json.load(open(os.path.join(ROOT,'data','theme_linkage_history.json'),encoding='utf-8'))
    except Exception:
        lk={}
    themes=cand.get('themes') or []
    members={}
    for t in themes:
        srcs=list(t.get('sources') or [])+list(((t.get('resolution') or {}).get('selected')) or [])
        t2=dict(t); t2['sources']=srcs
        try: members[t.get('id')]=set(members_for(t2,a.date))
        except Exception: members[t.get('id')]=set()
    dd=dedup(themes,members)
    start=(datetime.strptime(a.date,'%Y%m%d')-timedelta(days=180)).strftime('%Y%m%d')
    blocks=[]
    for t in themes:
        mem=sorted(dd.eligible(t.get('id')))
        base={}
        for c in mem:
            q=px.get(c) or {}; l=lm.get(c) or {}; ch=cm.get(c) or {}
            base[c]={'code':c,'pct':q.get('pct'),'amount':q.get('amount'),
                     'limit':str(l.get('limit') or ''),'limit_times':_i(l.get('limit_times')),
                     'first_time':str(l.get('first_time') or ''),'open_times':_i(l.get('open_times')),
                     'fd_amount':_f(l.get('fd_amount')),
                     'winner':_f(ch.get('winner_rate')) if ch else None,
                     'cost85':_f(ch.get('cost_85pct')) if ch else None}
        # 候选池：成交额前15 + 涨幅前8 + 旧leader/ladder，避免只看涨停
        pool=set(sorted(mem,key=lambda c:-(base[c].get('amount') or 0))[:15])
        pool |= set(sorted(mem,key=lambda c:-(base[c].get('pct') or -99))[:8])
        prior=[(t.get('leader') or {}).get('code')]+[x.get('code') for x in (t.get('ladder') or [])]
        pool |= {c for c in prior if c in base}
        bars=_fetch_bars_multi(sorted(pool),start,a.date,a.date) if pool else {}
        rows=[]
        for c in pool:
            r=dict(base[c]); dfb=bars.get(c)
            if dfb is not None and len(dfb)>=21:
                dfb=dfb.sort_values('trade_date'); close=[float(x) for x in dfb['close']]
                high=[float(x) for x in dfb['high']]
                r['ret5']=round(close[-1]/close[-6]-1,4) if len(close)>6 else None
                r['ret20']=round(close[-1]/close[-21]-1,4) if len(close)>=21 else None
                r['dist60']=round(close[-1]/max(high[-60:])-1,4) if high else None
                r['prior_leader']=c in prior
            rows.append(r)
        rows.sort(key=lambda x:-(x.get('amount') or 0))
        # 题材证据：首联/套牢/位置/量价
        old=lk.get(t.get('id')) or []
        first_linkage=not any(x.get('state')=='linked' for x in old)
        wins=[x['winner'] for x in rows if x.get('winner') is not None]
        pvs=[x['close']/x['cost85']-1 for x in rows if x.get('cost85') and x.get('close')]
        r20s=[x.get('ret20') for x in rows if x.get('ret20') is not None]
        ev={'first_linkage':first_linkage,'n_prior_linked':sum(1 for x in old if x.get('state')=='linked'),
            'overhang_winner':round(sum(wins)/len(wins),2) if wins else None,
            'overhang_pv85':round(sum(pvs)/len(pvs),4) if pvs else None,
            'avg_ret20':round(sum(r20s)/len(r20s),4) if r20s else None,
            'breadth':round(sum(1 for x in rows if (x.get('pct') or 0)>0)/len(rows),3) if rows else None}
        blocks.append({'theme_id':t.get('id'),'name':t.get('name'),'sources':[(s.get('code'),s.get('name')) for s in t.get('sources') or []],
                       'resolver_leader':t.get('leader'),'evidence':ev,'members':rows})
    prompt='''你是题材龙头判定器。只输出 JSON，不要解释。对每个题材，从给定 members 中选：
- leader：1 只，题材龙头/辨识度最高
- ladder：2 只，龙二/龙三
- decision：accept 或 review
- reasons：只能引用 members 里的 pct/limit/limit_times/first_time/open_times/amount/winner/ret5/ret20/dist60/prior_leader
- playbook：first_linkage / low_first_volume / high_acceleration / confirm
- counter_evidence：不确定原因
不得选 members 之外的代码。
狼大硬规则（必须遵守）：
1) leader 优先板块内总市值前5 + 营收拐点/扭亏/增速加速 + 题材联动不可替换的核心标；
2) 核心候选里找辨识度最高老龙头/风向标，老龙头不等于当日最强；
3) 涨停/封单/首板只确认还活着，不能作为选 leader 的主排序，不要只按 limit_times/pct 排序；
4) 配套段（连接器/电源/结构件/轴承/材料/加工设备/汽车配件/链传动）只能 ladder，不能 leader；
5) 没有核心龙头就 leader=null、decision=review，不许用配套股/后排替补。evidence.first_linkage=true 表示首联，首日可以选未涨停但最先转强的龙头；evidence.overhang_winner 过高/overhang_pv85 过深时降低置信度。
输入：%s
输出：
{"themes":[{"theme_id":"","playbook":"first_linkage|low_first_volume|high_acceleration|confirm","leader":{"code":"","name":""},"ladder":[{"code":"","name":""}],"decision":"accept|review","confidence":0.0,"reasons":[""],"counter_evidence":[""]}]}
''' % json.dumps(blocks,ensure_ascii=False)
    import requests
    url=os.getenv('WOLF_V3_AGENT_URL') or os.getenv('PI_SERVER_URL') or 'http://dsh:3001/chat'
    try:
        r=requests.post(url,json={'message':prompt,'session_id':'theme_leader_%s'%a.date},headers={'Content-Type':'application/json'},timeout=a.timeout,verify=False)
        r.raise_for_status(); raw=r.json().get('reply','')
    except Exception as e:
        print(json.dumps({'ok':False,'error':'agent_call_failed','detail':str(e)[:200]},ensure_ascii=False)); return 2
    obj=extract(raw)
    if not obj: print(json.dumps({'ok':False,'error':'agent_bad_json','raw':raw[:500]},ensure_ascii=False)); return 3
    by={b['theme_id']:set(x['code'] for x in b['members']) for b in blocks}
    out=[]
    for x in obj.get('themes') or []:
        tid=x.get('theme_id'); valid=by.get(tid,set())
        def ok(y): return isinstance(y,dict) and y.get('code') in valid
        lead=x.get('leader') if ok(x.get('leader')) else None
        if lead:
            try:
                from app.services.wolf_leader_rules import check as _lc
                _ok,_why=_lc(lead.get('code'))
                if not _ok:
                    lead=None
                    x.setdefault('counter_evidence',[]).append('wolf_core_gate:'+_why)
                    x['decision']='review'
            except Exception:
                pass
        out.append({'theme_id':tid,'playbook':x.get('playbook'),
                    'leader':lead,
                    'ladder':[z for z in (x.get('ladder') or []) if ok(z)],
                    'decision':x.get('decision') or 'review','confidence':x.get('confidence'),
                    'reasons':x.get('reasons') or [],'counter_evidence':x.get('counter_evidence') or []})
    res={'ok':True,'date':a.date,'themes':out,'raw':raw[:4000]}
    p=os.path.join(ROOT,'data','theme_candidate_leader_agent.json')
    json.dump(res,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    try:
        from theme_candidate_store import save
        save(a.date,'leader_agent',res)
    except Exception as e: print('[pg] leader_agent save fail',str(e)[:100])
    print(json.dumps(res,ensure_ascii=False,indent=1)[:5000]); print('written',p)
    return 0
if __name__=='__main__': raise SystemExit(main())
