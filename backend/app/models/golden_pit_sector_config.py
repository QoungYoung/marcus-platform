# -*- coding: utf-8 -*-
"""黄金坑板块拆分配置模型（key-value 表，供页面弹窗运行时配置）。"""

from sqlalchemy import Column, String, Integer, Text
from app.database import Base


class GoldenPitSectorConfig(Base):
    """黄金坑板块拆分配置项：key → value（bool/number 由 value_type 标识）。"""

    __tablename__ = "golden_pit_sector_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_key = Column(String(60), nullable=False, unique=True, comment="配置键")
    config_value = Column(Text, nullable=False, default="", comment="配置值（字符串存储；JSON 载体配置可能超 100 字符）")
    label = Column(String(80), nullable=False, default="", comment="展示名称")
    description = Column(Text, nullable=True, comment="说明")
    value_type = Column(String(20), nullable=False, default="number", comment="类型: bool / number")
    sort_order = Column(Integer, nullable=False, default=0, comment="排序")
