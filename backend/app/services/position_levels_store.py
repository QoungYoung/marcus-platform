# -*- coding: utf-8 -*-
"""position_levels_store.py — agent 上下位落 PG。"""
from __future__ import annotations
import os


TABLE = "position_levels"


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
            CREATE TABLE IF NOT EXISTS position_levels (
                account_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                down_trigger NUMERIC,
                up_trigger NUMERIC,
                status TEXT,
                source TEXT NOT NULL DEFAULT 'v3_agent',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (account_id, symbol)
            )
            """)


def upsert(account_id: str, symbol: str, down, up, status: str = "", source: str = "v3_agent"):
    ensure()
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
            INSERT INTO position_levels(account_id,symbol,down_trigger,up_trigger,status,source,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,now())
            ON CONFLICT (account_id,symbol) DO UPDATE SET
              down_trigger=EXCLUDED.down_trigger,
              up_trigger=EXCLUDED.up_trigger,
              status=EXCLUDED.status,
              source=EXCLUDED.source,
              updated_at=now()
            """, (account_id, symbol, down, up, status, source))


def load(account_id: str = "stock"):
    ensure()
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""SELECT symbol,down_trigger,up_trigger,status,updated_at
                           FROM position_levels WHERE account_id=%s""", (account_id,))
            return {str(r[0]): {"down": r[1], "up": r[2], "status": r[3], "updated_at": r[4]} for r in cur.fetchall()}
