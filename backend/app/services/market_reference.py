# -*- coding: utf-8 -*-
"""Market reference data read repository (PostgreSQL).

Replaces the previous per-file ``sqlite3.connect`` blocks against
``data/stock_pool.db`` / ``data/cache.db`` / ``data/trades.db``.

All functions degrade gracefully (return None / []) on database errors so
callers keep their existing fallback behavior.
"""
import time
from typing import Optional

from sqlalchemy import func, or_

from app.database import SessionLocal
from app.models.market_orm import (
    StockPool,
    Sector,
    StockConceptMap,
    StockSectorMap,
    EtfPool,
    SectorConfig,
)

# ---- short TTL caches for hot lookups (remote PG round-trips are expensive) ----
_NAME_CACHE: dict = {}
_NAME_CACHE_TS: float = 0.0
_INDUSTRY_CACHE: dict = {}
_INDUSTRY_CACHE_TS: float = 0.0
_TTL = 300  # seconds


def _ts_code_of(symbol: str) -> str:
    """Map a Marcus/plain symbol to a Tushare ts_code (600519 -> 600519.SH)."""
    s = symbol.strip().upper()
    if "." in s:
        return s
    if s[:2] in ("SH", "SZ", "BJ"):
        s = s[2:]
    if s.startswith("6") or s.startswith("9"):
        return f"{s}.SH"
    if s.startswith(("0", "3")):
        return f"{s}.SZ"
    if s.startswith(("4", "8")):
        return f"{s}.BJ"
    return f"{s}.SH"


def _plain_code(symbol: str) -> str:
    """SH600519 -> 600519; 600519.SH -> 600519; keep plain 6-digit as-is."""
    s = symbol.strip().upper()
    if "." in s:
        s = s.split(".")[0]
    if s[:2] in ("SH", "SZ", "BJ") and len(s) > 2:
        s = s[2:]
    return s


def get_stock_name(symbol: str) -> Optional[str]:
    """Look up Chinese stock name from the PostgreSQL stock_pool table."""
    if not symbol:
        return None
    code = _plain_code(symbol)
    now = time.time()
    if now - _NAME_CACHE_TS > _TTL:
        _NAME_CACHE.clear()
    if code in _NAME_CACHE:
        return _NAME_CACHE[code]
    name = None
    try:
        db = SessionLocal()
        try:
            row = db.query(StockPool.name).filter(
                or_(StockPool.symbol == code, StockPool.ts_code == _ts_code_of(code))
            ).first()
            if row and row.name:
                name = row.name.strip()
            if not name:
                # ETF 名称回退：etf_pool（symbol 带 SH/SZ 前缀）→ golden_pit_etf_config（etf_code）
                sym_upper = symbol.strip().upper()
                candidates = [sym_upper, "SH" + code, "SZ" + code, "BJ" + code]
                etf_row = db.query(EtfPool.name).filter(
                    EtfPool.symbol.in_(candidates)
                ).first()
                if etf_row and etf_row.name:
                    name = etf_row.name.strip()
            if not name:
                from app.models.golden_pit_etf_config import GoldenPitETFConfig
                cfg_row = db.query(GoldenPitETFConfig.etf_name).filter(
                    GoldenPitETFConfig.etf_code.in_(candidates)
                ).first()
                if cfg_row and cfg_row.etf_name:
                    name = cfg_row.etf_name.strip()
        finally:
            db.close()
    except Exception:
        pass
    _NAME_CACHE[code] = name
    return name


def get_stock_industry(symbol: str) -> Optional[str]:
    """Look up industry from the PostgreSQL stock_pool table."""
    if not symbol:
        return None
    code = _plain_code(symbol)
    now = time.time()
    if now - _INDUSTRY_CACHE_TS > _TTL:
        _INDUSTRY_CACHE.clear()
    if code in _INDUSTRY_CACHE:
        return _INDUSTRY_CACHE[code]
    industry = None
    try:
        db = SessionLocal()
        try:
            row = db.query(StockPool.industry).filter(
                or_(StockPool.symbol == code, StockPool.ts_code == _ts_code_of(code))
            ).first()
            if row:
                industry = row.industry
        finally:
            db.close()
    except Exception:
        pass
    _INDUSTRY_CACHE[code] = industry
    return industry


def get_stock_name_by_ts_code(ts_code: str) -> Optional[str]:
    """Look up stock name by ts_code (e.g. 600519.SH)."""
    if not ts_code:
        return None
    try:
        db = SessionLocal()
        try:
            row = db.query(StockPool.name).filter(StockPool.ts_code == ts_code).first()
            return row.name if row else None
        finally:
            db.close()
    except Exception:
        return None


def search_stocks(q: str, limit: int = 30) -> list:
    """Search A-share stocks by symbol/name/ts_code, non-ST, by market cap."""
    q_lower = q.strip().lower()
    if not q_lower:
        return []
    try:
        db = SessionLocal()
        try:
            rows = (
                db.query(
                    StockPool.symbol,
                    StockPool.name,
                    StockPool.industry,
                    StockPool.market,
                    StockPool.market_cap,
                )
                .filter(
                    StockPool.is_st == 0,
                    or_(
                        func.lower(StockPool.symbol).like(f"%{q_lower}%"),
                        func.lower(StockPool.name).like(f"%{q_lower}%"),
                        func.lower(StockPool.ts_code).like(f"%{q_lower}%"),
                    ),
                )
                .order_by(StockPool.market_cap.desc().nullslast())
                .limit(limit)
                .all()
            )
            return [
                {
                    "symbol": r.symbol,
                    "name": r.name,
                    "industry": r.industry or "",
                    "market": r.market,
                    "market_cap": r.market_cap or 0,
                }
                for r in rows
            ]
        finally:
            db.close()
    except Exception:
        return []


def get_concepts(limit: int = 30) -> dict:
    """List concept sectors (name + stock_count) plus total count."""
    try:
        db = SessionLocal()
        try:
            rows = (
                db.query(Sector.sector_name, Sector.stock_count)
                .filter(Sector.sector_type == "concept")
                .order_by(Sector.stock_count.desc())
                .limit(limit)
                .all()
            )
            total = db.query(func.count(Sector.id)).filter(Sector.sector_type == "concept").scalar() or 0
            return {
                "concepts": [{"sector_name": r.sector_name, "stock_count": r.stock_count} for r in rows],
                "total": total,
            }
        finally:
            db.close()
    except Exception:
        return {"concepts": [], "total": 0}


def get_concept_stocks(concept: str, limit: int = 30) -> dict:
    """Stocks under a concept plus the concept sector info."""
    concept = concept.strip()
    try:
        db = SessionLocal()
        try:
            rows = (
                db.query(
                    StockPool.ts_code, StockPool.symbol, StockPool.name, StockPool.market_cap
                )
                .join(StockConceptMap, StockConceptMap.ts_code == StockPool.ts_code)
                .filter(StockConceptMap.concept_name == concept)
                .order_by(StockPool.market_cap.desc().nullslast())
                .limit(limit)
                .all()
            )
            concept_row = (
                db.query(Sector.sector_name, Sector.stock_count)
                .filter(Sector.sector_name == concept)
                .first()
            )
            return {
                "concept": concept,
                "stock_count": concept_row.stock_count if concept_row else 0,
                "stocks": [
                    {
                        "ts_code": r.ts_code,
                        "symbol": r.symbol,
                        "name": r.name,
                        "market_cap": r.market_cap or 0,
                    }
                    for r in rows
                ],
                "concepts": (
                    [{"sector_name": concept_row.sector_name, "stock_count": concept_row.stock_count}]
                    if concept_row
                    else []
                ),
                "total": 1 if concept_row else 0,
            }
        finally:
            db.close()
    except Exception:
        return {"concept": concept, "stock_count": 0, "stocks": [], "concepts": [], "total": 0}


def get_component_stocks(sector_name: str, sector_type: str = "concept", limit: int = 10) -> list:
    """Top-N component ts_codes of a sector by market cap.

    sector_type: "concept" -> stock_concept_map join; "industry" -> stock_pool.industry.
    """
    try:
        db = SessionLocal()
        try:
            if sector_type == "concept":
                rows = (
                    db.query(StockPool.ts_code)
                    .join(StockConceptMap, StockConceptMap.ts_code == StockPool.ts_code)
                    .filter(StockConceptMap.concept_name == sector_name, StockPool.is_st == 0)
                    .order_by(StockPool.market_cap.desc().nullslast())
                    .limit(limit)
                    .all()
                )
            else:
                rows = (
                    db.query(StockPool.ts_code)
                    .filter(StockPool.industry == sector_name, StockPool.is_st == 0)
                    .order_by(StockPool.market_cap.desc().nullslast())
                    .limit(limit)
                    .all()
                )
            return [r.ts_code for r in rows]
        finally:
            db.close()
    except Exception:
        return []


def get_all_stock_pool() -> list:
    """All tradable stocks (non-ST, with market cap and industry) for in-memory grouping."""
    try:
        db = SessionLocal()
        try:
            rows = (
                db.query(
                    StockPool.ts_code, StockPool.symbol, StockPool.name,
                    StockPool.industry, StockPool.market_cap,
                )
                .filter(
                    StockPool.is_st == 0,
                    StockPool.market_cap > 0,
                    StockPool.industry.isnot(None),
                    StockPool.industry != "",
                )
                .all()
            )
            return [
                {
                    "ts_code": r.ts_code,
                    "symbol": r.symbol,
                    "name": r.name,
                    "industry": r.industry or "",
                    "market_cap": r.market_cap or 0,
                }
                for r in rows
            ]
        finally:
            db.close()
    except Exception:
        return []


def get_etf_pool(sector: Optional[str] = None, limit: int = 100) -> list:
    """ETF pool from the PostgreSQL etf_pool table."""
    try:
        db = SessionLocal()
        try:
            query = db.query(EtfPool)
            if sector:
                query = query.filter(EtfPool.sector == sector)
            rows = query.order_by(EtfPool.priority).limit(limit).all()
            return [
                {
                    "symbol": r.symbol,
                    "name": r.name,
                    "sector": r.sector,
                    "catalyst_type": r.catalyst_type,
                    "priority": r.priority,
                    "data": r.data or {},
                }
                for r in rows
            ]
        finally:
            db.close()
    except Exception:
        return []


def get_sector_config(key: Optional[str] = None) -> list:
    """Sector linkage config from the PostgreSQL sector_config table (JSONB -> dict)."""
    try:
        db = SessionLocal()
        try:
            query = db.query(SectorConfig)
            if key:
                query = query.filter(SectorConfig.sector_key == key)
            rows = query.order_by(SectorConfig.sector_key).all()
            return [
                {
                    "sector_key": r.sector_key,
                    "name": r.name,
                    "indices": r.indices or [],
                    "etfs": r.etfs or [],
                    "weight": r.weight,
                    "stocks": r.stocks or [],
                    "etf_codes": r.etf_codes or [],
                    "updated_at": r.updated_at,
                }
                for r in rows
            ]
        finally:
            db.close()
    except Exception:
        return []
def get_first_concept(symbol: str) -> Optional[str]:
    """First concept board a stock belongs to (used for RSR calculation)."""
    code = _plain_code(symbol)
    if not code:
        return None
    try:
        db = SessionLocal()
        try:
            row = (
                db.query(StockConceptMap.concept_name)
                .filter(StockConceptMap.ts_code.like(f"%{code}%"))
                .first()
            )
            return row.concept_name if row else None
        finally:
            db.close()
    except Exception:
        return None


def get_stock_names_by_ts_codes(ts_codes: list) -> dict:
    """Batch name lookup by ts_code: returns {ts_code: name}."""
    codes = [str(c) for c in ts_codes if c]
    if not codes:
        return {}
    try:
        db = SessionLocal()
        try:
            rows = (
                db.query(StockPool.ts_code, StockPool.name)
                .filter(StockPool.ts_code.in_(codes))
                .all()
            )
            return {r.ts_code: r.name for r in rows}
        finally:
            db.close()
    except Exception:
        return {}


def get_stock_concepts(symbol: str) -> set:
    """Set of concept board names a stock belongs to."""
    code = _plain_code(symbol)
    if not code:
        return set()
    try:
        db = SessionLocal()
        try:
            rows = (
                db.query(StockConceptMap.concept_name)
                .filter(StockConceptMap.ts_code.like(f"%{code}%"))
                .all()
            )
            return {r.concept_name for r in rows}
        finally:
            db.close()
    except Exception:
        return set()
