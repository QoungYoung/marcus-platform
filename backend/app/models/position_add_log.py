# -*- coding: utf-8 -*-
"""加仓监控日志模型 — PostgreSQL 持久化，用于页面展示和事后审计。"""
from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from app.database import Base


class PositionAddMonitorLog(Base):
    __tablename__ = "position_add_monitor_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    symbol = Column(String(16), nullable=False, index=True)

    current_tier = Column(String(10), default="probe")
    target_tier = Column(String(10), default="")
    action = Column(String(20), default="")
    result = Column(String(20), nullable=False, index=True)

    float_pnl_pct = Column(Float, default=0)
    current_price = Column(Float, default=0)
    add_shares = Column(Integer, default=0)
    block_reason = Column(String(500), default="")

    gate_details = Column(JSONB, default=list)
    trend_details = Column(JSONB, default=dict)
