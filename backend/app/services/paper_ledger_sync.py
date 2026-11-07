# -*- coding: utf-8 -*-
"""paper_ledger_sync.py — stock 账户 paper 成交同步 PG。"""
from __future__ import annotations
import os
from datetime import date

def _sym(s: str) -> str:
    from app.services.t_data_sources import std_symbol
    return std_symbol(s)

def sync(order: dict, account: str = "stock") -> None:
    import psycopg2
    sym=_sym(order.get("symbol") or "")
    vol=int(order.get("volume") or 0)
    px=float(order.get("price") or 0)
    side=str(order.get("direction") or order.get("side") or "")
    is_buy = ("买" in side) or side.lower()=="buy" or int(order.get("signed") or 0)>0
    if not sym or vol<=0 or px<=0: return
    with psycopg2.connect(os.environ["DATABASE_URL"]) as c:
        with c.cursor() as cur:
            cur.execute("""
            insert into paper_orders(orderid,symbol,direction,price,volume,status,traded,created_at,updated_at,reason,account_id)
            values(%s,%s,%s,%s,%s,'全部成交',%s,now(),now(),%s,%s)
            on conflict(orderid) do nothing
            """,(order.get("order_id") or order.get("orderid"),sym,side,px,vol,vol,order.get("reason") or "",account))
            cur.execute("select volume,COALESCE(avg_price,0),entry_date from paper_positions where account_id=%s and symbol=%s",(account,sym))
            r=cur.fetchone()
            if is_buy:
                if r:
                    nv=int(r[0])+vol; avg=(float(r[1])*int(r[0])+px*vol)/nv if nv else px
                    cur.execute("update paper_positions set volume=%s,avg_price=%s,updated_at=now() where account_id=%s and symbol=%s",(nv,avg,account,sym))
                else:
                    cur.execute("insert into paper_positions(symbol,entry_date,highest_price,updated_at,volume,frozen,avg_price,account_id) values(%s,%s,%s,now(),%s,0,%s,%s)",(sym,date.today(),px,vol,px,account))
            else:
                if r:
                    nv=max(0,int(r[0])-vol)
                    if nv==0:
                        cur.execute("delete from paper_positions where account_id=%s and symbol=%s",(account,sym))
                    else:
                        cur.execute("update paper_positions set volume=%s,updated_at=now() where account_id=%s and symbol=%s",(nv,account,sym))
