# -*- coding: utf-8 -*-
"""theme_universe_store.py — 题材宇宙 PG source of truth。

表：
  theme_definition(theme_id PK, name, sleeve, status, version, payload jsonb, updated_at)
  theme_membership(theme_id, as_of_date, symbol, source, PK(theme_id,as_of_date,symbol,source))
"""
from __future__ import annotations
import json, os
from datetime import date
from typing import Any, Dict, List, Optional


def _conn():
    import psycopg2
    dsn = os.getenv("DATABASE_URL")
    if not dsn: raise RuntimeError("DATABASE_URL missing")
    return psycopg2.connect(dsn, connect_timeout=3)


def ensure():
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS theme_definition (
                theme_id TEXT PRIMARY KEY,
                name TEXT,
                sleeve TEXT NOT NULL DEFAULT 'theme',
                status TEXT NOT NULL DEFAULT 'active',
                version INT NOT NULL DEFAULT 1,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS track_definition (
                track_id TEXT PRIMARY KEY,
                name TEXT,
                sleeve TEXT NOT NULL DEFAULT 'track',
                status TEXT NOT NULL DEFAULT 'active',
                version INT NOT NULL DEFAULT 1,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS theme_membership (
                theme_id TEXT NOT NULL,
                as_of_date DATE NOT NULL,
                symbol TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (theme_id, as_of_date, symbol, source)
            )
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS theme_membership_core (
                theme_id TEXT NOT NULL,
                as_of_date DATE NOT NULL,
                symbol TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                member_role TEXT NOT NULL DEFAULT 'core',
                main_business_ratio DOUBLE PRECISION,
                main_business_reason TEXT,
                PRIMARY KEY (theme_id, as_of_date, symbol)
            )
            """)


def save_themes(themes: List[Dict[str, Any]], satellite: bool = False, status: str = "active") -> int:
    ensure()
    n = 0
    with _conn() as c:
        with c.cursor() as cur:
            for t in themes or []:
                tid = str(t.get("id") or "").strip()
                if not tid: continue
                sleeve = "satellite" if satellite else (t.get("sleeve") or "theme")
                cur.execute("""
                INSERT INTO theme_definition(theme_id,name,sleeve,status,version,payload,updated_at)
                VALUES (%s,%s,%s,%s,1,%s::jsonb,now())
                ON CONFLICT (theme_id) DO UPDATE SET
                  name=EXCLUDED.name, sleeve=EXCLUDED.sleeve, status=EXCLUDED.status,
                  payload=EXCLUDED.payload, version=theme_definition.version+1, updated_at=now()
                """, (tid, t.get("name"), sleeve, status, json.dumps(t, ensure_ascii=False)))
                n += 1
    return n


def save_tracks(tracks: List[Dict[str, Any]], satellite: bool = False) -> int:
    ensure(); n = 0
    with _conn() as c:
        with c.cursor() as cur:
            for t in tracks or []:
                tid = str(t.get("id") or "").strip()
                if not tid: continue
                cur.execute("""
                INSERT INTO track_definition(track_id,name,sleeve,status,version,payload,updated_at)
                VALUES (%s,%s,%s,'active',1,%s::jsonb,now())
                ON CONFLICT (track_id) DO UPDATE SET
                  name=EXCLUDED.name, sleeve=EXCLUDED.sleeve, payload=EXCLUDED.payload,
                  version=track_definition.version+1, updated_at=now()
                """, (tid, t.get("name"), "satellite" if satellite else (t.get("sleeve") or "track"),
                      json.dumps(t, ensure_ascii=False)))
                n += 1
    return n


def load_track_config() -> Dict[str, Any]:
    ensure()
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT track_id,sleeve,payload FROM track_definition WHERE status='active' ORDER BY track_id")
            rows = cur.fetchall()
    tracks, sat = [], []
    for tid, sleeve, payload in rows:
        t = payload if isinstance(payload, dict) else json.loads(payload)
        t = dict(t); t["id"] = tid
        if sleeve == "satellite": sat.append(t)
        else: tracks.append(t)
    if not tracks and not sat: return {}
    return {"generated": True, "source": "postgres:track_definition", "tracks": tracks, "satellite_tracks": sat}


def load_theme_config() -> Dict[str, Any]:
    """返回与 universe_theme.yaml 兼容的结构；库为空时返回 {}。"""
    ensure()
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT theme_id,sleeve,status,payload FROM theme_definition WHERE status='active' ORDER BY theme_id")
            rows = cur.fetchall()
    themes, sat = [], []
    for tid, sleeve, status, payload in rows:
        t = payload if isinstance(payload, dict) else json.loads(payload)
        t = dict(t); t["id"] = tid
        if sleeve == "satellite": sat.append(t)
        else: themes.append(t)
    if not themes and not sat: return {}
    return {"generated": True, "source": "postgres:theme_definition", "themes": themes, "satellite_themes": sat}


def load_members(theme_id: str, as_of: str) -> set:
    ensure()
    d = "%s-%s-%s" % (as_of[:4], as_of[4:6], as_of[6:8]) if len(as_of) == 8 else as_of
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT symbol FROM theme_membership WHERE theme_id=%s AND as_of_date=%s", (theme_id, d))
            return {str(r[0]) for r in cur.fetchall()}


def load_core_members(theme_id: str, as_of: str) -> set:
    ensure()
    d = "%s-%s-%s" % (as_of[:4], as_of[4:6], as_of[6:8]) if len(as_of) == 8 else as_of
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT symbol FROM theme_membership_core WHERE theme_id=%s AND as_of_date=%s", (theme_id, d))
            return {str(r[0]) for r in cur.fetchall()}


def save_core_members(theme_id: str, members, as_of: Optional[str] = None, source: str = "main_business") -> int:
    ensure()
    d = as_of or date.today().strftime("%Y-%m-%d")
    d = "%s-%s-%s" % (d[:4], d[4:6], d[6:8]) if len(d) == 8 else d
    n = 0
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("DELETE FROM theme_membership_core WHERE theme_id=%s AND as_of_date=%s", (theme_id, d))
            for m in members or []:
                sym = m.get("symbol") if isinstance(m, dict) else m
                if not sym:
                    continue
                cur.execute("""INSERT INTO theme_membership_core(theme_id,as_of_date,symbol,source,member_role,main_business_ratio,main_business_reason)
                               VALUES (%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (theme_id,as_of_date,symbol) DO UPDATE SET
                               source=EXCLUDED.source,member_role=EXCLUDED.member_role,
                               main_business_ratio=EXCLUDED.main_business_ratio,main_business_reason=EXCLUDED.main_business_reason""",
                            (theme_id, d, str(sym), source,
                             (m.get("role") if isinstance(m, dict) else "core") or "core",
                             m.get("ratio") if isinstance(m, dict) else None,
                             m.get("reason") if isinstance(m, dict) else None))
                n += 1
    return n


def save_members(theme_id: str, symbols, as_of: Optional[str] = None, source: str = "resolver") -> int:
    ensure()
    d = as_of or date.today().strftime("%Y-%m-%d")
    d = "%s-%s-%s" % (d[:4], d[4:6], d[6:8]) if len(d) == 8 else d
    n = 0
    with _conn() as c:
        with c.cursor() as cur:
            for s in symbols or []:
                cur.execute("""
                INSERT INTO theme_membership(theme_id,as_of_date,symbol,source) VALUES (%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
                """, (theme_id, d, str(s), source)); n += 1
    return n
