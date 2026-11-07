#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_ab_worker.py — 消费 theme_ab_trigger，调 A/B，写 theme_ab_signals。"""
from __future__ import annotations
import argparse, json, os, re, sys, time
from datetime import datetime
from theme_ab_common import load_holding,load_wave,validate_b
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,os.path.join(ROOT,'backend'))
sys.path.insert(0,ROOT)
sys.path.insert(0,os.path.join(ROOT,'core'))

def db():
    import psycopg2
    return psycopg2.connect(os.environ['DATABASE_URL'])

def ensure(c):
    with c.cursor() as cur:
        cur.execute("""
        create table if not exists theme_ab_trigger (
          trade_date date not null, slot text not null, status text not null default 'pending',
          created_at timestamptz default now(), primary key(trade_date,slot)
        );
        create table if not exists theme_ab_signals (
          trade_date date not null, slot text not null, source text, target text,
          leader text, wind jsonb, mid jsonb, mode text, status text, reason text,
          a_raw jsonb, b_raw jsonb, created_at timestamptz default now(),
          primary key(trade_date, slot)
        )""")

def call(prompt, sid, timeout=1200):
    import requests
    url=os.getenv('WOLF_V3_AGENT_URL','http://dsh:3001/chat')
    r=requests.post(url,json={'message':prompt,'session_id':f'{sid}_{int(time.time()*1000)}'},timeout=timeout)
    r.raise_for_status(); raw=r.json().get('reply','')
    i=raw.find('{')
    if i<0: raise RuntimeError('no json')
    obj,end=json.JSONDecoder().raw_decode(raw[i:])
    return obj,raw

def prompt(name):
    from app.services.agent_prompt_store import load_prompt
    return load_prompt(name)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--once',action='store_true'); ap.add_argument('--limit',type=int,default=1)
    ap.add_argument('--date',default=''); ap.add_argument('--apply',action='store_true'); ap.add_argument('--trade',action='store_true'); a=ap.parse_args()
    while True:
        c=db(); ensure(c); c.commit()
        with c.cursor() as cur:
            if a.date:
                cur.execute("select trade_date,slot from theme_ab_trigger where status='pending' and trade_date=%s order by slot limit %s",(a.date,a.limit))
            else:
                cur.execute("select trade_date,slot from theme_ab_trigger where status='pending' order by trade_date,slot limit %s",(a.limit,))
            rows=cur.fetchall()
        for td,slot in rows:
            day=td.strftime('%Y%m%d')
            with c.cursor() as cur:
                cur.execute("update theme_ab_trigger set status='running' where trade_date=%s and slot=%s and status='pending'",(td,slot))
            c.commit()
            try:
                apath=next((os.path.join(ROOT,'data/asof',f'{day}_{x}.json') for x in ('a_intraday','a_facts') if os.path.exists(os.path.join(ROOT,'data/asof',f'{day}_{x}.json'))),None)
                if not apath:
                    import subprocess
                    subprocess.run([sys.executable,os.path.join(ROOT,'jobs/theme_intraday_live.py'),'--date',day],env=os.environ,check=False,timeout=900)
                    apath=next((os.path.join(ROOT,'data/asof',f'{day}_{x}.json') for x in ('a_intraday','a_facts') if os.path.exists(os.path.join(ROOT,'data/asof',f'{day}_{x}.json'))),None)
                if not apath: raise RuntimeError('no A data')
                holding=load_holding(); wave=load_wave(day)
                A,araw=call(prompt('a_source')+f"\n\n数据：{apath}\n当前持仓：{json.dumps(holding,ensure_ascii=False)}\n浪型：{json.dumps(wave,ensure_ascii=False)}\n只输出 JSON。",f'ab_a_{day}_{slot}')
                target=''; leader=''; wind=[]; mid=[]; mode='none'; status='ok' if not A.get('need_switch') else 'proposed'
                braw={}
                if A.get('need_switch'):
                    bpath=os.path.join(ROOT,'data/asof',f'{day}_b_candidates.json')
                    if not os.path.exists(bpath): raise RuntimeError('no B data')
                    B,braw=call(prompt('b_target')+f"\n\n数据：{bpath}\nA候选：{json.dumps(A.get('candidates') or [],ensure_ascii=False)}\n当前持仓：{json.dumps(holding,ensure_ascii=False)}\n浪型：{json.dumps(wave,ensure_ascii=False)}\n只输出 JSON。",f'ab_b_{day}_{slot}')
                    target=B.get('target') or ''; leader=(B.get('roles') or {}).get('leader') or ''
                    wind=(B.get('roles') or {}).get('wind') or []; mid=(B.get('roles') or {}).get('mid') or []
                    mode=B.get('mode') or ''
                    _ok,_why=validate_b(B)
                    if not _ok:
                        status='rejected'; A['b_validation']=_why
                with c.cursor() as cur:
                    cur.execute("""insert into theme_ab_signals(trade_date,slot,source,target,leader,wind,mid,mode,status,reason,a_raw,b_raw)
                    values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    on conflict(trade_date,slot) do update set source=excluded.source,target=excluded.target,leader=excluded.leader,
                    wind=excluded.wind,mid=excluded.mid,mode=excluded.mode,status=excluded.status,reason=excluded.reason,
                    a_raw=excluded.a_raw,b_raw=excluded.b_raw""",(td,slot,A.get('source'),target,leader,json.dumps(wind),json.dumps(mid),mode,status,
                    A.get('verdict','')[:500],json.dumps(A,ensure_ascii=False),json.dumps(braw,ensure_ascii=False)))
                    cur.execute("update theme_ab_trigger set status='done' where trade_date=%s and slot=%s",(td,slot))
                c.commit()
                print(day,slot,'A_switch',A.get('need_switch'),'target',target,'leader',leader,'status',status,flush=True)
                if a.apply and status in ('proposed','approved'):
                    import subprocess
                    cmd=[sys.executable,os.path.join(ROOT,'jobs/theme_ab_apply.py'),'--date',day]
                    if a.trade: cmd.append('--trade')
                    subprocess.run(cmd,env=os.environ,check=False)
            except Exception as e:
                c.rollback()
                with c.cursor() as cur:
                    cur.execute("update theme_ab_trigger set status='error' where trade_date=%s and slot=%s",(td,slot))
                    cur.execute("""insert into theme_ab_signals(trade_date,slot,status,reason) values(%s,%s,'error',%s)
                    on conflict(trade_date,slot) do update set status='error',reason=excluded.reason""",(td,slot,str(e)[:500]))
                c.commit(); print(day,slot,'ERROR',str(e)[:200],file=sys.stderr,flush=True)
        c.close()
        if a.once: break
        time.sleep(30)
if __name__=='__main__': main()
