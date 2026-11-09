# -*- coding: utf-8 -*-
"""theme_ab_common.py — A/B worker 共用：PG、prompt、持仓、浪型、校验。"""
from __future__ import annotations
import json, os, re, sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,os.path.join(ROOT,'backend'))
sys.path.insert(0,ROOT)
sys.path.insert(0,os.path.join(ROOT,'core'))

def connect():
    import psycopg2
    return psycopg2.connect(os.environ['DATABASE_URL'])

def prompt(name):
    from app.services.agent_prompt_store import load_prompt
    return load_prompt(name)

def call(prompt_text, sid, timeout=600):
    import requests, time
    url=os.getenv('WOLF_V3_AGENT_URL','http://dsh:3001/chat')
    r=requests.post(url,json={'message':prompt_text,'session_id':f'{sid}_{int(time.time()*1000)}'},timeout=timeout)
    r.raise_for_status(); raw=r.json().get('reply','')
    i=raw.find('{')
    if i<0: raise RuntimeError('no json')
    obj,end=json.JSONDecoder().raw_decode(raw[i:])
    return obj,raw

def _canon(sym):
    s=str(sym or '').upper().replace(' ','')
    for p in ('SH','SZ','BJ'):
        if s.startswith(p): s=s[2:]; break
    if '.' in s: return s
    if s.startswith(('6','9','5')): return s+'.SH'
    if s.startswith(('4','8','92')): return s+'.BJ'
    return s+'.SZ'

def load_holding(account='stock'):
    try:
        c=connect(); cur=c.cursor()
        cur.execute("select symbol,volume,avg_price from paper_positions where account_id=%s and volume>0",(account,))
        rows=[{'symbol':_canon(r[0]),'volume':int(r[1]),'entry':float(r[2]),'themes':[]} for r in cur.fetchall()]
        syms=[r['symbol'] for r in rows]
        if syms:
            cur.execute("select symbol,theme_id from theme_membership where symbol=any(%s) and as_of_date=(select max(as_of_date) from theme_membership)",(syms,))
            mp={}
            for sym,tid in cur.fetchall(): mp.setdefault(_canon(sym),[]).append(tid)
            for r in rows: r['themes']=sorted(set(mp.get(r['symbol']) or []))
        c.close(); return rows
    except Exception:
        return []

def load_wave(date8):
    p=os.path.join(ROOT,'data','mainline',date8,'track.json')
    try:
        x=json.load(open(p,encoding='utf-8'))
        return {k:x.get(k) for k in ('mainline','mainline_status','regime','tradeable') if k in x}
    except Exception:
        return {}

def valid_symbol(s):
    return bool(isinstance(s,str) and re.match(r'^\d{6}\.(SH|SZ|BJ)$',s))

def validate_b(B):
    """B 输出基础校验；核心段由 B 的 leader_segment 自报，support 直接拒绝。"""
    if not B.get('target'): return False,'no_target'
    leader=(B.get('roles') or {}).get('leader') or ''
    if not valid_symbol(leader): return False,'leader_not_code'
    seg=str(B.get('leader_segment') or '').lower()
    if seg in ('support','配套','配套段'): return False,'leader_support_segment'
    try:
        from app.services.wolf_leader_rules import check
        ok, why = check(leader)
        if not ok: return False, why
    except Exception:
        pass
    return True,'ok'
