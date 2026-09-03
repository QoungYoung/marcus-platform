# -*- coding: utf-8 -*-
"""计划库模型(PostgreSQL) — 狼大主题计划/关键位回补计划 落地 DB。"""
from sqlalchemy import Column, String, Text
from app.database import Base


class Plan(Base):
    __tablename__ = "plan_library"
    id = Column(String(80), primary_key=True)
    type = Column(String(30), nullable=False)           # theme_plan / refill_plan
    subject = Column(String(80))
    horizon = Column(String(20))
    created_at = Column(String(20))
    status = Column(String(20), default="armed", index=True)  # armed / fired / expired
    thesis = Column(Text)
    trigger = Column(Text)        # JSON 字符串
    action = Column(String(200))
    gate = Column(String(200))
    pit_score = Column(Text)      # JSON
    today_context = Column(Text)  # JSON
    fired_at = Column(String(30))
    fire_reason = Column(Text)
