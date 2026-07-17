# -*- coding: utf-8 -*-
"""系统状态键值表 — 持久化运行时状态（峰值权益等）。"""
from sqlalchemy import Column, String, Float, DateTime
from datetime import datetime
from app.database import Base


class SystemState(Base):
    __tablename__ = "system_state"

    key = Column(String(64), primary_key=True)
    value = Column(String(1024), nullable=False, default='')
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
