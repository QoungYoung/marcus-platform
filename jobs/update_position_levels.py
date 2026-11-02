#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""update_position_levels.py — 每日用 v3 agent 更新持仓上下位并落库。"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timedelta

ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT,"backend"), os.path.join(ROOT,"core"), os.path.join(ROOT,"jobs")):
    if p not in sys.path: sys.path.insert(0,p)


def ts_code(sym):
    s=str(sym).upper().replace(".SH","").replace(".SZ","")
    if s.startswith(("SH","SZ")): s=s[2:]
    return s + (".SH" if s.startswith("6") else ".SZ")


def levels(sym, q):
    from app.services.wolf_v3_production import build_snapshot, ask_agent, validate_decision
    from tushare_relay import relay_query
    code=ts_code(sym); px=float(q.get("current") or 0)
    end=datetime.now().strftime("%Y%m%d"); start=(datetime.now()-timedelta(days=120)).strftime("%Y%m%d")
    df=relay_query("daily",ts_code=code,start_date=start,end_date=end,limit=120)
    snap=build_snapshot(code,q,[] if df is None else df.to_dict("records"))
    dec=ask_agent(snap,timeout=45)
    if not dec.get("ok"): return None
    v=validate_decision(dec.get("decision") or {},snap)
    d=v.get("decision") or dec.get("decision") or {}
    bt=d.get("break_trigger") or {}; down=float(bt.get("price") or 0) or None
    up=None; rz=d.get("rebound_zone") or {}; z=rz.get("zone") or []
    if len(z)==2: up=float(z[1]) or None
    if up is None:
        cs=[c for c in (snap.get("candidates") or []) if float(c.get("price") or 0)>px and str(c.get("state") or "") in ("resistance","broken","holding")]
        cs.sort(key=lambda c: abs(float(c.get("price") or 0)-px))
        if cs: up=float(cs[0].get("price") or 0) or None
    return {"down":down,"up":up,"status":str(bt.get("status_text") or "")[:200]}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--account",default="stock"); a=ap.parse_args()
    import psycopg2
    from app.services.t_data_sources import fetch_quote_one
    from app.services.position_levels_store import upsert
    c=psycopg2.connect(os.environ["DATABASE_URL"]); cur=c.cursor()
    cur.execute("select symbol,volume from paper_positions where account_id=%s and volume>0",(a.account,))
    rows=cur.fetchall(); c.close()
    out=[]
    for sym,vol in rows:
        q=fetch_quote_one(sym) or {}
        if float(q.get("current") or 0)<=0:
            out.append({"symbol":sym,"ok":False,"error":"no quote"}); continue
        lv=levels(sym,q)
        if not lv:
            out.append({"symbol":sym,"ok":False,"error":"agent"}); continue
        upsert(a.account,sym,lv["down"],lv["up"],lv["status"],"v3_agent")
        out.append({"symbol":sym,"volume":vol,"down":lv["down"],"up":lv["up"],"ok":True})
    print(json.dumps(out,ensure_ascii=False,indent=1))
    return 0


if __name__=="__main__": raise SystemExit(main())
