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
