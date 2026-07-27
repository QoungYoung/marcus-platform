# -*- coding: utf-8 -*-
"""黄金坑 DCA  ETF 配置模型。

定义黄金坑信号对应的可交易 ETF 及其定投策略参数。
"""

from sqlalchemy import Column, String, Float, Integer, Boolean, Text
from app.database import Base


class GoldenPitETFConfig(Base):
    """黄金坑指数 → ETF 映射 + DCA 策略配置。"""

    __tablename__ = "golden_pit_etf_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    fund_code = Column(String(10), nullable=False, unique=True, comment="宽基指数代码（即 ETF 代码）")
    index_name = Column(String(50), nullable=False, comment="指数名称")
    etf_code = Column(String(10), nullable=False, comment="ETF 交易代码（如 SH510050）")
    etf_name = Column(String(80), nullable=False, comment="ETF 全称")
    priority = Column(Integer, nullable=False, default=99, comment="入坑顺序（越小越早入坑）")

    # 定投策略
    strategy = Column(
        String(20), nullable=False, default="uniform_10",
        comment="DCA 策略: lump_entry / uniform_3 / uniform_5 / uniform_7 / uniform_10 / uniform_15 / front_loaded / back_loaded / triangle"
    )
    daily_amount = Column(Float, nullable=False, default=5000.0, comment="每次定投金额（元）")
    max_total_amount = Column(Float, nullable=False, default=50000.0, comment="单次黄金坑窗口最大投入总额（元）")
    max_position_pct = Column(Float, nullable=False, default=15.0, comment="单 ETF 仓位上限（%）")

    # 触发条件
    require_absolute_threshold = Column(
        Boolean, nullable=False, default=True,
        comment="是否要求绝对阈值(0.35)触发，False 则 P10 也触发"
    )
    min_days_in_pit = Column(Integer, nullable=False, default=0, comment="入坑至少 N 天后才开始定投")
    skip_if_already_holding = Column(
        Boolean, nullable=False, default=True,
        comment="已持仓时跳过（避免重复建仓）"
    )

    enabled = Column(Boolean, nullable=False, default=True, comment="是否启用")

    notes = Column(Text, nullable=True, comment="备注")
