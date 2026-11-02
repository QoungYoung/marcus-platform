#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_sleeve_store.py — 题材 facts/selected/trade 落 PG。"""
from __future__ import annotations
import json, os

TABLE = "theme_sleeve_daily"


def _conn():
    import psycopg2
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL missing")
    return psycopg2.connect(dsn)


def ensure():
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS theme_sleeve_daily (
                trade_date DATE PRIMARY KEY,
                facts JSONB,
                selected JSONB,
                trade JSONB,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_theme_sleeve_daily_updated ON theme_sleeve_daily(updated_at DESC)")


def save(date8: str, kind: str, payload) -> bool:
    """kind: facts / selected / trade"""
    if kind not in ("facts", "selected", "trade"):
        raise ValueError(kind)
    ensure()
    d = "%s-%s-%s" % (date8[:4], date8[4:6], date8[6:8])
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(f"""
            INSERT INTO {TABLE} (trade_date, {kind}, updated_at)
            VALUES (%s, %s::jsonb, now())
            ON CONFLICT (trade_date) DO UPDATE
            SET {kind} = EXCLUDED.{kind}, updated_at = now()
            """, (d, json.dumps(payload, ensure_ascii=False)))
    return True
