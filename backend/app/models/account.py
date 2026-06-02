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
    market_value: float
    floating_pnl: float
    floating_pnl_pct: float
    entry_date: str


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
    positions: List[PositionResponse] = []
    updated_at: datetime


class PortfolioSummary(BaseModel):
    account: AccountResponse
    total_return: float
    total_return_pct: float
    win_rate: float
