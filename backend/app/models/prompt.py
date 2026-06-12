# -*- coding: utf-8 -*-
"""
Prompt 表 — 存储所有 AI Agent 的 system prompt。
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from app.database import Base


class Prompt(Base):
    __tablename__ = "prompts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False, index=True)  # e.g. 'CHAT_SYSTEM_PROMPT'
    label = Column(String(200), nullable=True)                            # 中文名：'聊天模式'
    content = Column(Text, nullable=False)                                # 完整 prompt 文本
    version = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Prompt(name={self.name}, version={self.version})>"
