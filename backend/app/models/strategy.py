# -*- coding: utf-8 -*-
"""Strategy models."""
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel


class StrategyResponse(BaseModel):
    stance: str
    stance_code: str
    position_limit: int
    stop_loss: float
    take_profit: float
    trailing_stop: float
    sentiment_score: float
    sentiment_label: str
    gap_risk: Optional[Dict[str, Any]]
    fund_flow: Optional[Dict[str, Any]]
    watchlist: List[Dict[str, Any]] = []
    updated_at: datetime


class ScanResponse(BaseModel):
    scan_time: datetime
    stance: str
    stance_code: str
    position_limit: int
    sentiment_score: float
    hot_concepts: List[str]
    watchlist: List[str]
    sector_allocation: Dict[str, Any]
    gap_risk: Optional[Dict[str, Any]]


class ScanHistoryResponse(BaseModel):
    scans: List[ScanResponse]
    total: int
