# -*- coding: utf-8 -*-
"""theme_snapshot_store.py — theme.json 的 PG 版本。"""
from __future__ import annotations
import json, os, math

TABLE = "theme_snapshot"

def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)): return None
    if isinstance(v, dict): return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)): return [_clean(x) for x in v]
    return v

def _conn():
    import psycopg2
    return psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=3)

def ensure():
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS theme_snapshot (
                trade_date DATE PRIMARY KEY,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")

def save(date8: str, payload) -> bool:
    ensure(); d="%s-%s-%s"%(date8[:4],date8[4:6],date8[6:8])
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""INSERT INTO theme_snapshot(trade_date,payload,updated_at)
            VALUES (%s,%s::jsonb,now()) ON CONFLICT (trade_date) DO UPDATE
            SET payload=EXCLUDED.payload, updated_at=now()""",
                        (d,json.dumps(_clean(payload),ensure_ascii=False,allow_nan=False)))
    return True

def load(date8: str):
    ensure(); d="%s-%s-%s"%(date8[:4],date8[4:6],date8[6:8])
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT payload FROM theme_snapshot WHERE trade_date=%s",(d,))
            r=cur.fetchone(); return r[0] if r else None
