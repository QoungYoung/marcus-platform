#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""apply_agent_leader_to_facts.py — 把 agent leader/ladder 写进 facts roles。"""
from __future__ import annotations
import argparse, json, os, sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT,os.path.join(ROOT,'jobs'),os.path.join(ROOT,'backend'),os.path.join(ROOT,'core')):
    if p not in sys.path: sys.path.insert(0,p)
from theme_sleeve_store import load, save


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',default='20261102'); a=ap.parse_args()
    data=load(a.date,'facts')
    if not data: print('no facts'); return 2
    try:
        ag=json.load(open(os.path.join(ROOT,'data','theme_candidate_leader_agent.json'),encoding='utf-8'))
    except Exception as e:
        print('no agent leader',e); return 3
    by={x.get('theme_id'):x for x in ag.get('themes') or []}
    try:
        from tushare_relay import relay_query
        _d=relay_query('daily',trade_date=a.date,limit=6000)
        _l=relay_query('limit_list_d',trade_date=a.date,limit=6000)
        _pm={str(r.get('ts_code')):{'pct':float(r.get('pct_chg') or 0)} for _,r in _d.iterrows()} if _d is not None else {}
        _lm={str(r.get('ts_code')):r.to_dict() for _,r in _l.iterrows()} if _l is not None else {}
    except Exception:
        _pm={}; _lm={}
    def _intraday(code):
        try:
            from app.services.t_data_sources import fetch_tencent_mkline
            sym=("sh" if code.endswith(".SH") else "sz")+code.split(".")[0]
            bars=fetch_tencent_mkline(sym,freq="m1",count=500) or []
            v1330=vopen=0.0
            for b in bars:
                t=str(b.get("time") or "")
                hm=t[11:16] if len(t)>=16 else (t[-4:-2]+":"+t[-2:] if len(t)>=12 else "")
                if "13:30"<=hm<"13:35": v1330+=float(b.get("vol") or 0)
                elif "09:30"<=hm<"10:00": vopen+=float(b.get("vol") or 0)
            return {"symbol":code,"v1330":round(v1330,0),"vopen":round(vopen,0),"r":round(v1330/vopen,3) if vopen else None}
        except Exception:
            return {"symbol":code,"v1330":0,"vopen":0,"r":None}
    def _det(code):
        l=_lm.get(code) or {}; p=_pm.get(code) or {}
        return {'symbol':code,'pct':p.get('pct'),'first_time':str(l.get('first_time') or ''),'open_times':int(l.get('open_times') or 0) if l else None,'fd_amount':float(l.get('fd_amount') or 0) if l else None,'limit_times':int(l.get('limit_times') or 0) if l else None}
    n=0
    for t in data.get('themes') or []:
        tid=t.get('theme_id'); x=by.get(tid)
        if not x or not x.get('leader'): continue
        try:
            from app.services.theme_universe_store import load_members
            mem=set(load_members(tid,a.date))
        except Exception:
            mem=set()
        if not mem:
            mem=set(str(c) for c in [t.get('leader')]+list((t.get('roles') or {}).get('wind') or [])+list((t.get('roles') or {}).get('mid') or []) if c)
        code=(x.get('leader') or {}).get('code')
        if code not in mem:
            print('skip not member',tid,code); continue
        try:
            from app.services.wolf_leader_rules import check as _lc
            _ok,_why=_lc(code)
            if not _ok:
                print('skip wolf core gate',tid,code,_why); continue
        except Exception:
            pass
        t['roles']=t.get('roles') or {}
        t['roles']['leader']=code
        t['leader']=code
        ladder=[z.get('code') for z in (x.get('ladder') or []) if z.get('code') in mem]
        t['roles']['wind']=ladder[:2]
        # mid 保留不在 leader/ladder 的
        t['roles']['mid']=[z for z in (t['roles'].get('mid') or []) if z not in ([code]+ladder)]
        rd=t.get('role_detail') or {}
        rd['leader']=_det(code)
        rd['wind']=[_det(z) for z in ladder if z]
        t['role_detail']=rd
        # 重建 intraday/sync，必须只用本题材的 leader+wind+mid
        codes=[code]+ladder+t['roles'].get('mid',[])
        vals=[_intraday(c) for c in dict.fromkeys(codes) if c]
        t['intraday']={'n':len(vals),'roles':vals}
        rs=[z.get('r') for z in vals if z.get('r') is not None]
        t['internal_linkage']=t.get('internal_linkage') or {}
        t['internal_linkage']['sync']={'mean':round(sum(rs)/len(rs),2) if rs else None,'n':len(rs),'roles':[{'symbol':z['symbol'],'r':z.get('r')} for z in vals]}
        t['leader_source']='agent'
        t['agent_leader_confidence']=x.get('confidence')
        n+=1
        print('applied',tid,code,'wind',ladder)
    save(a.date,'facts',data)
    p=os.path.join(ROOT,'data','mainline',a.date,'theme_sleeve_facts.json')
    try: json.dump(data,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    except Exception: pass
    print('done',n)
    return 0
if __name__=='__main__': raise SystemExit(main())
