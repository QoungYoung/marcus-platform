# -*- coding: utf-8 -*-
"""风向标每日结果 PostgreSQL 持久化。"""
from __future__ import annotations
import json, math
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from app.database import engine

_TABLE = "bellwether_daily"
_READY = False


def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


def ensure_table() -> None:
    global _READY
    if _READY:
        return
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                trade_date VARCHAR(8) NOT NULL,
                subtheme VARCHAR(64) NOT NULL,
                parent VARCHAR(64),
                bellwether_code VARCHAR(16),
                bellwether_name VARCHAR(64),
                state VARCHAR(16),
                second_line_new BOOLEAN NOT NULL DEFAULT FALSE,
                high_cut_low BOOLEAN NOT NULL DEFAULT FALSE,
                hold_second_line BOOLEAN NOT NULL DEFAULT FALSE,
                reduce_theme BOOLEAN NOT NULL DEFAULT FALSE,
                risk VARCHAR(16),
                reasons JSONB,
                metrics JSONB,
                source VARCHAR(32),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (trade_date, subtheme)
            )
        """))
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_state ON {_TABLE}(trade_date, state)"))
    _READY = True


def upsert_items(date8: str, items: List[Dict[str, Any]], source: str = "unified") -> int:
    ensure_table()
    n = 0
    with engine.begin() as conn:
        for it in items:
            p = it.get("permissions") or {}
            reasons = it.get("reasons") or []
            metrics = {k: it.get(k) for k in ("close", "ma20", "low20", "r5", "sub_r5", "rel5",
                                               "amt_ratio", "breadth", "lower_high", "vol_dist",
                                               "successor", "successor_code", "impulse") if k in it}
            conn.execute(text(f"""
                INSERT INTO {_TABLE}
                    (trade_date, subtheme, parent, bellwether_code, bellwether_name, state,
                     second_line_new, high_cut_low, hold_second_line, reduce_theme, risk,
                     reasons, metrics, source)
                VALUES
                    (:d, :sub, :parent, :code, :name, :state,
                     :second, :hcl, :hold, :reduce, :risk,
                     CAST(:reasons AS JSONB), CAST(:metrics AS JSONB), :source)
                ON CONFLICT (trade_date, subtheme) DO UPDATE SET
                    parent=EXCLUDED.parent, bellwether_code=EXCLUDED.bellwether_code,
                    bellwether_name=EXCLUDED.bellwether_name, state=EXCLUDED.state,
                    second_line_new=EXCLUDED.second_line_new, high_cut_low=EXCLUDED.high_cut_low,
                    hold_second_line=EXCLUDED.hold_second_line, reduce_theme=EXCLUDED.reduce_theme,
                    risk=EXCLUDED.risk, reasons=EXCLUDED.reasons, metrics=EXCLUDED.metrics,
                    source=EXCLUDED.source, created_at=now()
            """), {
                "d": str(date8), "sub": it.get("subtheme"), "parent": it.get("parent"),
                "code": it.get("bellwether_code"), "name": it.get("bellwether_name"),
                "state": it.get("state"), "second": bool(p.get("second_line_new")),
                "hcl": bool(p.get("high_cut_low")), "hold": bool(p.get("hold_second_line")),
                "reduce": bool(p.get("reduce_theme")), "risk": p.get("risk"),
                "reasons": json.dumps(_clean(reasons), ensure_ascii=False),
                "metrics": json.dumps(_clean(metrics), ensure_ascii=False), "source": source,
            })
            n += 1
    return n


def latest(date8: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_table()
    q = f"SELECT * FROM {_TABLE}"
    args = {}
    if date8:
        q += " WHERE trade_date = :d"; args["d"] = str(date8)
    q += " ORDER BY subtheme"
    with engine.connect() as conn:
        rs = conn.execute(text(q), args).fetchall()
    return [dict(r._mapping) for r in rs]
