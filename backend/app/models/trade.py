# -*- coding: utf-8 -*-
"""Trade and Order models."""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class TradeRequest(BaseModel):
    symbol: str
    side: str  # "buy" or "sell"
    price: float
    volume: int
    reason: Optional[str] = ""


class TradeResponse(BaseModel):
    order_id: str
    status: str
    symbol: str
    direction: str
    price: float
    volume: int
    amount: float
    timestamp: datetime
    reason: Optional[str] = None
    message: Optional[str] = None


class OrderResponse(BaseModel):
    order_id: str
    symbol: str
    name: str = ""
    direction: str
    price: float
    volume: int
    status: str
    traded: int
    created_at: datetime
    updated_at: datetime
    id: Optional[int] = None
    reason: str = ""


class TradeHistoryResponse(BaseModel):
    trades: List[OrderResponse]
    total: int
    page: int
    page_size: int


class VoidRequest(BaseModel):
    reason: str


class VoidResponse(BaseModel):
    success: bool
    trade_id: int
    symbol: str = ""
    direction: str = ""
    volume: int = 0
    reason: str = ""
    error: str = ""
