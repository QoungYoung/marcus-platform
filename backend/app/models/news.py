# -*- coding: utf-8 -*-
"""News models."""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class NewsResponse(BaseModel):
    id: int
    title: str
    content: Optional[str]
    source: str
    publish_time: datetime
    sentiment: str
    sentiment_score: float
    category: Optional[str] = None          # 行业板块 (DB: category)
    industry: Optional[str] = None          # 行业板块 (DB: category, alias)
    keyword: Optional[str] = None            # 事件类型 (DB: keyword)
    concepts: List[str] = []                 # 热点概念 (DB: concepts, comma-separated)
    symbols: List[str] = []
    url: Optional[str] = None


class NewsListResponse(BaseModel):
    news: List[NewsResponse]
    total: int
    page: int
    page_size: int


class SentimentResponse(BaseModel):
    score: float
    positive_count: int
    negative_count: int
    neutral_count: int
    dominant_sentiment: str
    updated_at: datetime
