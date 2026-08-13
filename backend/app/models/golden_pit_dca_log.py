# -*- coding: utf-8 -*-
"""黄金坑 DCA 执行日志模型。"""

from sqlalchemy import Column, String, Float, Integer, Text
from app.database import Base


class GoldenPitDCALog(Base):
    """黄金坑 DCA 定投执行记录。"""

    __tablename__ = "golden_pit_dca_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    fund_code = Column(String(30), nullable=False, index=True, comment="宽基指数代码 / industry/<id>")
    window_start = Column(String(10), nullable=False, index=True, comment="黄金坑窗口起始日期")
    buy_day = Column(Integer, nullable=False, comment="窗口第几天买入")
    etf_code = Column(String(10), nullable=False, comment="ETF 交易代码")
    amount = Column(Float, nullable=False, comment="本次买入金额")
    strategy = Column(String(50), nullable=False, comment="使用的策略名称")
    order_id = Column(String(50), nullable=True, comment="交易系统订单号")
    status = Column(String(20), nullable=False, default="filled", comment="filled / failed / notified / aborted / safety_brake")
    created_at = Column(String(20), nullable=False, comment="创建时间")
    schedule_day = Column(Integer, nullable=True, comment="DCA窗口内第几天(0起始)")
    trend_factor = Column(Float, nullable=True, comment="当前趋势调节因子")
    notes = Column(Text, nullable=True, comment="备注")
