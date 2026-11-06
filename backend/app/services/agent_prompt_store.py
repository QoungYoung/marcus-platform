# -*- coding: utf-8 -*-
"""agent_prompt_store.py — A/B 提示词 PG 存储（唯一来源）。"""
from __future__ import annotations
import json, os
from typing import Optional

def _conn():
    import psycopg2
    return psycopg2.connect(os.environ["DATABASE_URL"])

def ensure_schema() -> None:
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
            create table if not exists agent_prompts (
              id bigserial primary key,
              name text not null,
              version text not null,
              content text not null,
              meta jsonb default '{}'::jsonb,
              active boolean default false,
              created_at timestamptz default now(),
              unique(name, version)
            );
            create unique index if not exists ux_agent_prompts_active
              on agent_prompts(name) where active;
            """)

def load_prompt(name: str) -> str:
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("select content from agent_prompts where name=%s and active=true", (name,))
            row = cur.fetchone()
    if not row:
        raise RuntimeError(f"active prompt not found: {name}")
    return row[0]

def put_prompt(name: str, version: str, content: str, meta: Optional[dict] = None, activate: bool = False) -> None:
    ensure_schema()
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
            insert into agent_prompts(name,version,content,meta,active)
            values(%s,%s,%s,%s,%s)
            on conflict(name,version) do update set content=excluded.content, meta=excluded.meta
            """, (name, version, content, json.dumps(meta or {}, ensure_ascii=False), bool(activate)))
            if activate:
                cur.execute("update agent_prompts set active=false where name=%s and version<>%s", (name, version))

def activate(name: str, version: str) -> None:
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("update agent_prompts set active=false where name=%s", (name,))
            cur.execute("update agent_prompts set active=true where name=%s and version=%s", (name, version))
            if cur.rowcount != 1:
                raise RuntimeError(f"prompt version not found: {name} {version}")

def list_prompts(name: str = "") -> list[dict]:
    with _conn() as c:
        with c.cursor() as cur:
            if name:
                cur.execute("select name,version,active,meta,created_at from agent_prompts where name=%s order by created_at desc", (name,))
            else:
                cur.execute("select name,version,active,meta,created_at from agent_prompts order by created_at desc")
            return [{"name":r[0],"version":r[1],"active":r[2],"meta":r[3],"created_at":str(r[4])} for r in cur.fetchall()]
