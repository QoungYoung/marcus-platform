"""资金流缓存表 — 定时落库，API 直读"""
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Index
from app.database import Base


class FundFlowCache(Base):
    __tablename__ = "fund_flow_cache"

    data_type = Column(String(32), primary_key=True, comment="individual / market / concept")
    symbol = Column(String(32), default="", primary_key=True, comment="个股代码/概念名/空")
    data_json = Column(Text, nullable=False, comment="JSON 序列化数据")
    updated_at = Column(DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        Index("idx_ffc_type_time", "data_type", "updated_at"),
    )
