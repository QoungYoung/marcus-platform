# -*- coding: utf-8 -*-
"""低位超额线样本表（PostgreSQL，不再依赖 eval_v3_fixed.json）。

表 low_position_samples：
  trade_date / ret / n / codes text[] / dirs text[] / top / gap / flags
"""
from __future__ import annotations
import json
import os
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.database import engine

_TABLE = "low_position_samples"
_READY = False


def ensure_table() -> None:
    global _READY
    if _READY:
        return
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                trade_date VARCHAR(8) PRIMARY KEY,
                ret DOUBLE PRECISION NOT NULL,
                n INTEGER NOT NULL DEFAULT 0,
                codes TEXT[] NOT NULL DEFAULT '{{}}',
                dirs TEXT[] NOT NULL DEFAULT '{{}}',
                top DOUBLE PRECISION,
                gap DOUBLE PRECISION,
                breadth2 BOOLEAN NOT NULL DEFAULT FALSE,
                market_ok BOOLEAN NOT NULL DEFAULT FALSE,
                strong BOOLEAN NOT NULL DEFAULT FALSE,
                weak BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
    _READY = True


def seed_from_json(path: str) -> int:
    """一次性迁移：把 eval_v3_fixed.json 的 rows 写入 DB（幂等 upsert）。"""
    ensure_table()
    x = json.load(open(path, encoding="utf-8"))
    rows = x.get("rows") or []
    n = 0
    with engine.begin() as conn:
        for r in rows:
            conn.execute(text(f"""
                INSERT INTO {_TABLE}
                    (trade_date, ret, n, codes, dirs, top, gap, breadth2, market_ok, strong, weak)
                VALUES
                    (:d, :ret, :n, :codes, :dirs, :top, :gap, :b2, :mok, :strong, :weak)
                ON CONFLICT (trade_date) DO UPDATE SET
                    ret=EXCLUDED.ret, n=EXCLUDED.n, codes=EXCLUDED.codes, dirs=EXCLUDED.dirs,
                    top=EXCLUDED.top, gap=EXCLUDED.gap, breadth2=EXCLUDED.breadth2,
                    market_ok=EXCLUDED.market_ok, strong=EXCLUDED.strong, weak=EXCLUDED.weak
            """), {
                "d": str(r.get("date") or ""), "ret": float(r.get("ret") or 0),
                "n": int(r.get("n") or 0), "codes": list(r.get("codes") or []),
                "dirs": list(r.get("dirs") or []), "top": r.get("top"), "gap": r.get("gap"),
                "b2": bool(r.get("breadth2")), "mok": bool(r.get("market_ok")),
                "strong": bool(r.get("strong")), "weak": bool(r.get("weak")),
            })
            n += 1
    return n


def last_valid() -> Dict[str, Any]:
    """最后一个 v3_strong 样本（strong 且非 breadth2）。"""
    ensure_table()
    with engine.connect() as conn:
        r = conn.execute(text(f"""
            SELECT trade_date, ret FROM {_TABLE}
            WHERE strong = TRUE AND breadth2 = FALSE
            ORDER BY trade_date DESC LIMIT 1
        """)).fetchone()
    if not r:
        return {"trade_date": "-", "ret": None}
    return {"trade_date": str(r[0]), "ret": float(r[1]) * 100.0}


def all_rows() -> List[Dict[str, Any]]:
    ensure_table()
    with engine.connect() as conn:
        rs = conn.execute(text(f"SELECT * FROM {_TABLE} ORDER BY trade_date")).fetchall()
    return [dict(r._mapping) for r in rs]
