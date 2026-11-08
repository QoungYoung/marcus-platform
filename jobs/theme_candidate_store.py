#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_candidate_store.py — 题材候选管线落 PG（discovery/cluster/agent/resolver/dedup）。"""
from __future__ import annotations
import json, math, os

TABLE = "theme_candidate_pipeline"


def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)): return None
    if isinstance(v, dict): return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)): return [_clean(x) for x in v]
    return v


def _conn():
    import psycopg2
    dsn = os.getenv("DATABASE_URL")
    if not dsn: raise RuntimeError("DATABASE_URL missing")
    return psycopg2.connect(dsn)


def ensure():
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS theme_candidate_pipeline (
                trade_date DATE NOT NULL,
                stage TEXT NOT NULL,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (trade_date, stage)
            )
            """)


def save(date8: str, stage: str, payload) -> bool:
    ensure()
    d = "%s-%s-%s" % (date8[:4], date8[4:6], date8[6:8])
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
            INSERT INTO theme_candidate_pipeline(trade_date,stage,payload,updated_at)
            VALUES (%s,%s,%s::jsonb,now())
            ON CONFLICT (trade_date,stage) DO UPDATE SET payload=EXCLUDED.payload, updated_at=now()
            """, (d, stage, json.dumps(_clean(payload), ensure_ascii=False, allow_nan=False)))
    return True


def load(date8: str, stage: str):
    ensure()
    d = "%s-%s-%s" % (date8[:4], date8[4:6], date8[6:8])
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT payload FROM theme_candidate_pipeline WHERE trade_date=%s AND stage=%s", (d, stage))
            row = cur.fetchone()
            return row[0] if row else None
