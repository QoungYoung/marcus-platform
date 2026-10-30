# -*- coding: utf-8 -*-
"""index_daily_store.py — 指数日线 PG 唯一数据源。"""
from __future__ import annotations
import os
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import create_engine, text

_ENGINE = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        url = os.environ.get("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
        _ENGINE = create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=2)
    return _ENGINE


SCHEMA = """
CREATE TABLE IF NOT EXISTS index_daily (
  ts_code VARCHAR(16) NOT NULL,
  trade_date DATE NOT NULL,
  open NUMERIC(18,4),
  high NUMERIC(18,4),
  low NUMERIC(18,4),
  close NUMERIC(18,4),
  vol NUMERIC(20,2),
  amount NUMERIC(20,2),
  src VARCHAR(32),
  updated_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (ts_code, trade_date)
)
"""

UPSERT = """
INSERT INTO index_daily (ts_code, trade_date, open, high, low, close, vol, amount, src)
VALUES (:ts_code, :trade_date, :open, :high, :low, :close, :vol, :amount, :src)
ON CONFLICT (ts_code, trade_date) DO UPDATE SET
  open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, close=EXCLUDED.close,
  vol=EXCLUDED.vol, amount=EXCLUDED.amount, src=EXCLUDED.src, updated_at=now()
"""


def ensure_table():
    with _engine().begin() as c:
        c.execute(text(SCHEMA))


def upsert_rows(code: str, rows: List[Dict[str, Any]], src: str) -> int:
    if not rows:
        return 0
    ensure_table()
    params = []
    for r in rows:
        d = str(r["date"])
        params.append({
            "ts_code": code,
            "trade_date": date(int(d[:4]), int(d[4:6]), int(d[6:8])),
            "open": float(r.get("open") or 0), "high": float(r.get("high") or 0),
            "low": float(r.get("low") or 0), "close": float(r.get("close") or 0),
            "vol": float(r.get("vol") or 0), "amount": float(r.get("amount") or 0),
            "src": src,
        })
    with _engine().begin() as c:
        c.execute(text(UPSERT), params)
    return len(params)


def load_rows(code: str) -> List[Tuple[str, float, float, float]]:
    """→ [(date8, close, high, amount)] 升序；DB 不可用返回 []。"""
    try:
        with _engine().connect() as c:
            rs = c.execute(text(
                "SELECT trade_date, close, high, amount FROM index_daily WHERE ts_code=:c ORDER BY trade_date"
            ), {"c": code}).fetchall()
        return [(r[0].strftime("%Y%m%d"), float(r[1]), float(r[2]), float(r[3] or 0)) for r in rs]
    except Exception:
        return []


def load_bars(code: str, days: Optional[int] = None) -> List[Dict[str, Any]]:
    try:
        with _engine().connect() as c:
            rs = c.execute(text(
                "SELECT trade_date, open, high, low, close, vol, amount FROM index_daily "
                "WHERE ts_code=:c ORDER BY trade_date"
            ), {"c": code}).fetchall()
        out = [{"date": r[0].strftime("%Y%m%d"), "open": float(r[1] or 0), "high": float(r[2] or 0),
                "low": float(r[3] or 0), "close": float(r[4] or 0), "vol": float(r[5] or 0),
                "amount": float(r[6] or 0)} for r in rs]
        return out[-days:] if days else out
    except Exception:
        return []


def latest_date(code: str = "000001.SH") -> Optional[str]:
    rows = load_rows(code)
    return rows[-1][0] if rows else None
