# -*- coding: utf-8 -*-
"""Account and Position models."""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class PositionResponse(BaseModel):
    symbol: str
    name: str
    volume: int
    avg_price: float
    current_price: float
    change_pct: float = 0      # 当日涨跌幅(%)
    today_pnl: float = 0       # 今日盈亏
    market_value: float
    floating_pnl: float
    floating_pnl_pct: float
    entry_date: str
    high_water_mark: Optional[float] = None    # 持仓期间最高价
    high_water_date: Optional[str] = None      # 达到最高价的日期
    days_since_high: Optional[int] = None      # 距上次创新高已过交易日数
    sector_rank: Optional[int] = None          # 同板块涨幅排名（第X名）
    sector_rank_pct: Optional[float] = None    # 同板块涨幅排名百分比（如 20%=前20%）


class AccountResponse(BaseModel):
    initial_capital: float
    available_cash: float
    frozen_cash: float
    position_value: float
    total_asset: float
    realized_pnl: float
    float_pnl: float
    total_pnl: float
    position_ratio: float
    week_realized_pnl: float = 0     # 本周已实现盈亏
    week_float_pnl: float = 0        # 本周持仓浮盈
    positions: List[PositionResponse] = []
    updated_at: datetime


class PortfolioSummary(BaseModel):
    account: AccountResponse
    total_return: float
    total_return_pct: float
    win_rate: float
    sector_concentration: Optional[dict] = None  # 板块集中度: {"sector": "xxx", "concentration_pct": 25.0, "high_corr_exposure_pct": 30.0}


class EquityPoint(BaseModel):
    date: str
    equity: float
    daily_pnl: float = 0.0


class DailyStockPnl(BaseModel):
    """个股当日盈亏明细"""
    symbol: str
    name: str = ""
    volume: int = 0
    close_price: float = 0.0
    prev_close: float = 0.0
    float_pnl: float = 0.0       # 浮动盈亏变动 = volume * (close - prev_close)
    realized_pnl: float = 0.0    # 当日卖出已实现盈亏


class CapitalAdjustRequest(BaseModel):
    """手动资金调整请求（amount > 0 为入金，< 0 为出金）"""
    amount: float
    note: str = ""


class DailyPnlBreakdown(BaseModel):
    """单日盈亏明细"""
    date: str
    daily_pnl: float = 0.0
    realized_total: float = 0.0
    float_total: float = 0.0
    stocks: List[DailyStockPnl] = []
