# -*- coding: utf-8 -*-
"""Market ORM models 鈥?market reference data persisted in PostgreSQL.

Covers: MarketDiagnosis (existing), plus stock pool master data, ETF pool,
and sector linkage config migrated from SQLite (stock_pool.db / cache.db / trades.db).
"""
from sqlalchemy import Column, String, Float, Text, Integer, Index
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


class MarketDiagnosis(Base):
    __tablename__ = "market_diagnosis"

    trade_date = Column(String(8), primary_key=True)
    state = Column(String(20), nullable=False)
    label = Column(String(50), nullable=False)
    suggestion = Column(String(200), nullable=False)
    score_trend = Column(Float, default=0)
    score_oscillation = Column(Float, default=0)
    score_extreme = Column(Float, default=0)
    indicators_json = Column(Text)
    created_at = Column(String(20), nullable=False)


class StockPool(Base):
    __tablename__ = "stock_pool"

    ts_code = Column(String(20), primary_key=True)
    symbol = Column(String(20), nullable=False)
    name = Column(String(50), nullable=False)
    area = Column(String(20))
    industry = Column(String(50), nullable=False)
    market = Column(String(10))
    list_date = Column(String(10))
    is_st = Column(Integer, default=0)
    market_cap = Column(Float)
    updated_at = Column(String(30), nullable=False)
    board = Column(String(20))

    __table_args__ = (
        Index("idx_industry", "industry"),
        Index("idx_market", "market"),
        Index("idx_is_st", "is_st"),
    )


class Sector(Base):
    __tablename__ = "sectors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sector_name = Column(String(50), unique=True, nullable=False)
    sector_type = Column(String(20), nullable=False)
    stock_count = Column(Integer, default=0)
    updated_at = Column(String(30), nullable=False)


class ConceptSector(Base):
    __tablename__ = "concept_sectors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    concept_name = Column(String(50), unique=True, nullable=False)
    keywords = Column(Text)
    updated_at = Column(String(30), nullable=False)


class StockConceptMap(Base):
    __tablename__ = "stock_concept_map"

    ts_code = Column(String(20), primary_key=True)
    concept_name = Column(String(50), primary_key=True)

    __table_args__ = (
        Index("idx_stock_concept", "concept_name"),
    )


class StockSectorMap(Base):
    __tablename__ = "stock_sector_map"

    ts_code = Column(String(20), primary_key=True)
    sector_name = Column(String(50), primary_key=True)

    __table_args__ = (
        Index("idx_sector", "sector_name"),
    )


class EtfPool(Base):
    __tablename__ = "etf_pool"

    symbol = Column(String(20), primary_key=True)
    name = Column(String(50))
    sector = Column(String(30))
    catalyst_type = Column(String(30))
    priority = Column(Integer, default=3)
    data = Column(JSONB)
    updated_at = Column(String(30), nullable=False)


class SectorConfig(Base):
    __tablename__ = "sector_config"

    sector_key = Column(String(30), primary_key=True)
    name = Column(String(50), nullable=False)
    indices = Column(JSONB, default=list)
    etfs = Column(JSONB, default=list)
    weight = Column(Float, default=0.5)
    stocks = Column(JSONB, default=list)
    updated_at = Column(String(30), nullable=False)
    etf_codes = Column(JSONB, default=list)