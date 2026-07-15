# -*- coding: utf-8 -*-
"""MarketDiagnosis ORM model — stores daily market structure diagnosis in PostgreSQL."""
from sqlalchemy import Column, String, Float, Text
from app.database import Base


class MarketDiagnosis(Base):
    __tablename__ = "market_diagnosis"

    trade_date = Column(String(8), primary_key=True)
    state = Column(String(20), nullable=False)
    label = Column(String(50), nullable=False)
    suggestion = Column(String(200), nullable=False)
    score_trend = Column(Float, default=0)
    score_oscillation = Column(Float, default=0)
    score_extreme = Column(Float, default=0)
    indicators_json = Column(Text)
    created_at = Column(String(20), nullable=False)
