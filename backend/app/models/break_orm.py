# -*- coding: utf-8 -*-
"""破位事件/动作账本（BreakStop v1 / P4）。

append-only：
  · `break_events`：每个 level 每日的状态（watch/intraday/close/confirmed/reclaimed）
  · `break_actions`：每日实际动作（alert/cap_50/...），执行状态单独更新

唯一键用语义键，不用浮点价格：
  events  (asof, symbol, level_type, status)
  actions (asof, symbol, action)
"""
from sqlalchemy import (BigInteger, Column, Date, DateTime, Index, Numeric, String,
                        Text, UniqueConstraint, func)

from app.database import Base


class BreakEvent(Base):
    __tablename__ = "break_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    asof = Column(Date, nullable=False, index=True)
    symbol = Column(String(16), nullable=False, index=True)
    domain = Column(String(10), nullable=False, default="index")
    level_type = Column(String(32), nullable=False, default="")
    level_price = Column(Numeric(14, 4), nullable=True)
    phase = Column(String(10), nullable=False, default="close")
    status = Column(String(20), nullable=False, default="none", index=True)
    close = Column(Numeric(14, 4), nullable=True)
    high = Column(Numeric(14, 4), nullable=True)
    low = Column(Numeric(14, 4), nullable=True)
    vol_ratio = Column(Numeric(10, 4), nullable=True)
    volume_branch = Column(String(10), nullable=True)
    wave_op = Column(String(10), nullable=True)
    position_pnl = Column(Numeric(10, 4), nullable=True)
    action = Column(String(20), nullable=True)
    target_pct = Column(Numeric(8, 4), nullable=True)
    reason = Column(Text, nullable=True)
    mode = Column(String(16), nullable=False, default="alert")
    data_warning = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("asof", "symbol", "level_type", "status",
                         name="uq_break_events_semantic"),
        Index("idx_break_events_asof_status", "asof", "status"),
    )


class BreakAction(Base):
    __tablename__ = "break_actions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    asof = Column(Date, nullable=False, index=True)
    symbol = Column(String(16), nullable=False, index=True)
    domain = Column(String(10), nullable=False, default="index")
    severity = Column(String(10), nullable=False, default="")
    level_type = Column(String(32), nullable=True)
    action = Column(String(20), nullable=False)
    target_pct = Column(Numeric(8, 4), nullable=True)
    target_is_cap = Column(String(1), nullable=False, default="1")
    block_new_buy = Column(String(1), nullable=False, default="0")
    allow_zero = Column(String(1), nullable=False, default="0")
    reason = Column(Text, nullable=True)
    invalidation = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="pending", index=True)
    trigger_id = Column(BigInteger, nullable=True)
    executed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("asof", "symbol", "action", name="uq_break_actions_semantic"),
        Index("idx_break_actions_asof_status", "asof", "status"),
    )
