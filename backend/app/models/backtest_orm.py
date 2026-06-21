# -*- coding: utf-8 -*-
"""
Backtest System - SQLAlchemy ORM Models
PostgreSQL persistence for all backtest data.
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime, Text,
    ForeignKey, UniqueConstraint, Index, Boolean, JSON,
)
from sqlalchemy.orm import relationship
from app.database import Base


class BacktestTask(Base):
    """回测任务主表"""
    __tablename__ = "backtest_tasks"

    id = Column(String(32), primary_key=True)
    name = Column(String(200), nullable=False, comment="任务名称")
    start_date = Column(Date, nullable=False, comment="起始日期")
    end_date = Column(Date, nullable=False, comment="结束日期")
    initial_capital = Column(Float, nullable=False, default=1_000_000, comment="初始资金")
    include_chinext = Column(Boolean, nullable=False, default=False, comment="是否包含创业板(300/301开头)")
    status = Column(String(20), nullable=False, default="pending",
                    comment="pending/running/completed/failed/cancelled")
    current_day = Column(Date, nullable=True, comment="当前模拟日期")
    total_days = Column(Integer, default=0, comment="总交易日数")
    completed_days = Column(Integer, default=0, comment="已完成交易日数")
    progress = Column(Float, default=0, comment="进度百分比")
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    # Relations
    logs = relationship("BacktestDailyLog", back_populates="task", cascade="all, delete-orphan")
    trades = relationship("BacktestTrade", back_populates="task", cascade="all, delete-orphan")
    positions = relationship("BacktestPosition", back_populates="task", cascade="all, delete-orphan")
    equity_snapshots = relationship("BacktestEquitySnapshot", back_populates="task", cascade="all, delete-orphan")
    monthly_metrics = relationship("BacktestMonthlyMetric", back_populates="task", cascade="all, delete-orphan")


class BacktestDailyLog(Base):
    """每日回测日志"""
    __tablename__ = "backtest_daily_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(32), ForeignKey("backtest_tasks.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True, comment="交易日期")
    day_index = Column(Integer, nullable=False, comment="第几个交易日")
    phase = Column(String(50), nullable=False, comment="阶段: pre_market/market_scan/pi_trade/daily_review")
    phase_time = Column(String(10), nullable=False, comment="阶段时间: 09:00/09:35/...")
    event_type = Column(String(50), nullable=False,
                        comment="事件类型: log/scan_report/pi_analysis/pi_trade/trade/error/equity")
    content = Column(Text, nullable=True, comment="事件内容")
    metadata_json = Column(JSON, nullable=True, comment="结构化元数据")
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    # Relation
    task = relationship("BacktestTask", back_populates="logs")

    __table_args__ = (
        Index("idx_btlog_task_date", "task_id", "trade_date"),
        Index("idx_btlog_task_date_phase", "task_id", "trade_date", "phase"),
    )


class BacktestTrade(Base):
    """回测交易记录"""
    __tablename__ = "backtest_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(32), ForeignKey("backtest_tasks.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True, comment="交易日期")
    symbol = Column(String(20), nullable=False, index=True, comment="股票代码")
    stock_name = Column(String(50), default="", comment="股票名称")
    direction = Column(String(10), nullable=False, comment="buy/sell")
    price = Column(Float, nullable=False)
    volume = Column(Integer, nullable=False, comment="股数")
    amount = Column(Float, nullable=False, comment="成交金额")
    commission = Column(Float, default=0, comment="手续费（已含买卖各自的费率）")
    profit = Column(Float, default=0, comment="实现盈亏（仅 sell 有值）")
    profit_pct = Column(Float, default=0, comment="盈亏百分比（仅 sell 有值）")
    reason = Column(Text, nullable=True, comment="交易理由（Pi决定的理由）")
    # ── 增强字段(2026-06) - 用于真假/滑点/税率评估 ──
    phase_time = Column(String(5), default="", comment="信号发出时刻 HH:MM")
    signal_price = Column(Float, default=0, comment="Pi 信号建议价（用于滑点评估）")
    actual_price = Column(Float, default=0, comment="实际成交价（同 price，冗余便于公式计算）")
    stamp_tax = Column(Float, default=0, comment="印花税（仅 sell, 0.1%）")
    transfer_fee = Column(Float, default=0, comment="过户费（沪市股票 0.001%）")
    slippage_pct = Column(Float, default=0, comment="滑点 = (actual-signal)/signal*100")
    net_profit = Column(Float, default=0, comment="净盈亏 = profit - stamp_tax - commission - transfer_fee")
    # T+1 违规标记 (仅用于历史数据诊断)
    is_t0_violation = Column(Boolean, default=False, comment="是否 T+0 违规 (当日买+当日卖, 旧引擎 bug)")
    t0_violation_note = Column(String(200), default="", comment="T+0 违规备注")
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    # Relation
    task = relationship("BacktestTask", back_populates="trades")

    __table_args__ = (
        Index("idx_bttrade_task_date", "task_id", "trade_date"),
    )


class BacktestPosition(Base):
    """回测持仓快照（每日收盘后）"""
    __tablename__ = "backtest_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(32), ForeignKey("backtest_tasks.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    trade_date = Column(Date, nullable=False, comment="日期")
    symbol = Column(String(20), nullable=False, comment="股票代码")
    volume = Column(Integer, nullable=False, comment="持仓股数")
    avg_cost = Column(Float, nullable=False, comment="平均成本")
    current_price = Column(Float, nullable=False, comment="当日收盘价")
    market_value = Column(Float, nullable=False, comment="市值")
    float_pnl = Column(Float, default=0, comment="浮动盈亏")
    float_pnl_pct = Column(Float, default=0, comment="浮动盈亏百分比")
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    # Relation
    task = relationship("BacktestTask", back_populates="positions")

    __table_args__ = (
        UniqueConstraint("task_id", "trade_date", "symbol", name="uq_btpos_task_date_symbol"),
        Index("idx_btpos_task_date", "task_id", "trade_date"),
    )


class BacktestEquitySnapshot(Base):
    """每日权益快照"""
    __tablename__ = "backtest_equity_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(32), ForeignKey("backtest_tasks.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    trade_date = Column(Date, nullable=False, comment="日期")
    total_asset = Column(Float, nullable=False, comment="总资产(含浮盈)")
    available_cash = Column(Float, nullable=False, comment="可用资金")
    position_value = Column(Float, default=0, comment="持仓市值(当前价)")
    cost_value = Column(Float, default=0, comment="持仓成本")
    float_pnl = Column(Float, default=0, comment="浮盈(当前价-成本)")
    cost_based_asset = Column(Float, default=0, comment="不含浮盈的总资产(现金+成本市值)")
    daily_pct = Column(Float, default=0, comment="当日收益率%(相对昨日)")
    daily_return = Column(Float, default=0, comment="累计收益率(相对初始)")
    cumulative_return = Column(Float, default=0, comment="累计收益率(相对初始)")
    baseline_return = Column(Float, default=0, comment="资产指数(首日=100,后续天=总资产/首日总资产×100)")
    cost_based_return = Column(Float, default=0, comment="不含浮盈的累计收益率(%)")
    intraday_low_equity = Column(Float, default=0, comment="当日盘中最低权益(分钟数据估算)")
    intraday_drawdown_pct = Column(Float, default=0, comment="当日盘中最大回撤%(收盘权益vs盘中最低)")
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    # Relation
    task = relationship("BacktestTask", back_populates="equity_snapshots")

    __table_args__ = (
        UniqueConstraint("task_id", "trade_date", name="uq_btequity_task_date"),
        Index("idx_btequity_task", "task_id"),
    )


class BacktestMonthlyMetric(Base):
    """月度绩效指标"""
    __tablename__ = "backtest_monthly_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(32), ForeignKey("backtest_tasks.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    month = Column(String(7), nullable=False, comment="月份 YYYY-MM")
    return_pct = Column(Float, default=0, comment="月度收益率")
    trades_count = Column(Integer, default=0, comment="交易笔数")
    win_count = Column(Integer, default=0, comment="盈利笔数")
    win_rate = Column(Float, default=0, comment="胜率")
    max_drawdown = Column(Float, default=0, comment="月内最大回撤")
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    # Relation
    task = relationship("BacktestTask", back_populates="monthly_metrics")

    __table_args__ = (
        UniqueConstraint("task_id", "month", name="uq_btmonthly_task_month"),
    )
