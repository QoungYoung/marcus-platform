# -*- coding: utf-8 -*-
"""Paper trading engine tables migrated from SQLite to PostgreSQL."""
from sqlalchemy import Column, String, Integer, Float, Text, DateTime
from app.database import Base


class PaperOrder(Base):
    __tablename__ = "paper_orders"

    orderid = Column(String(32), primary_key=True)
    symbol = Column(String(16), nullable=False, index=True)
    direction = Column(String(8), nullable=False)
    price = Column(Float, nullable=False)
    volume = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default="提交中")
    traded = Column(Integer, nullable=False, default=0)
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)
    reason = Column(String(256), default="")


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    orderid = Column(String(32), nullable=False)
    symbol = Column(String(16), nullable=False, index=True)
    direction = Column(String(8), nullable=False)
    price = Column(Float, nullable=False)
    volume = Column(Integer, nullable=False)
    amount = Column(Float, nullable=False)
    profit = Column(Float, default=0)
    created_at = Column(Text, nullable=False)
    trade_date = Column(Text, nullable=True)
    voided = Column(Integer, default=0)
    void_reason = Column(Text, nullable=True)
    voided_at = Column(Text, nullable=True)
    reason = Column(String(256), default="")


class PaperPosition(Base):
    __tablename__ = "paper_positions"

    symbol = Column(String(16), primary_key=True)
    entry_date = Column(Text, nullable=False)
    highest_price = Column(Float, default=0.0)
    updated_at = Column(Text, nullable=False)


class PaperAccountInfo(Base):
    __tablename__ = "paper_account_info"

    id = Column(Integer, primary_key=True)
    initial_capital = Column(Float, nullable=False)
    available_cash = Column(Float, nullable=False)
    frozen_cash = Column(Float, nullable=False, default=0)
    order_counter = Column(Integer, nullable=False, default=0)
    updated_at = Column(Text, nullable=False)


class PaperDailySnapshot(Base):
    __tablename__ = "paper_daily_snapshot"

    trade_date = Column(Text, primary_key=True)
    total_asset = Column(Float, nullable=False)
    available_cash = Column(Float, nullable=False)
    frozen_cash = Column(Float, default=0)
    position_value = Column(Float, default=0)
    cost_value = Column(Float, default=0)
    realized_pnl = Column(Float, default=0)
    float_pnl = Column(Float, default=0)
    total_pnl = Column(Float, default=0)
    initial_capital = Column(Float, nullable=False)
    created_at = Column(Text, nullable=False)
