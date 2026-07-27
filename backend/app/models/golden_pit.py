# -*- coding: utf-8 -*-
"""黄金坑每日快照模型 — 持久化每指数每日贪婪值，用于趋势回溯和阈值穿越检测。"""
from sqlalchemy import Column, Integer, String, Float, UniqueConstraint
from app.database import Base


class GoldenPitSnapshot(Base):
    __tablename__ = "golden_pit_snapshots"
    __table_args__ = (
        UniqueConstraint("date", "fund_code", name="uq_golden_pit_snapshot_date_code"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False, index=True)
    fund_code = Column(String(10), nullable=False)
    index_name = Column(String(50), nullable=False)
    greed_value = Column(Float, nullable=False)
    close_price = Column(Float, nullable=True)
    percentile = Column(Float, nullable=True)
    status = Column(String(20), nullable=False, default="normal")
    decline_rate_5d = Column(Float, nullable=True)
    created_at = Column(String(20), nullable=False)
