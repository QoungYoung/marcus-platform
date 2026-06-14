# -*- coding: utf-8 -*-
"""止损日志模型 — PostgreSQL 持久化，用于事后审计。"""
from sqlalchemy import Column, Integer, String, Float, DateTime
from app.database import Base


class StopLossLog(Base):
    __tablename__ = "stop_loss_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False)
    symbol = Column(String(20), nullable=False, index=True)
    rule = Column(String(50), nullable=False)       # 触发的规则编号: break_low / cost_stop / sector / iron_rule2 / dynamic / time_falsification
    price = Column(Float, nullable=False)
    volume = Column(Integer, nullable=False)
    float_pnl_pct = Column(Float, default=0)         # 触发时浮动盈亏(%)
    realized_profit = Column(Float, default=0)       # 实际盈亏(元)
    executed = Column(String(10), default='yes')     # yes / no / failed
    reason = Column(String(500), default='')         # 触发详细原因
