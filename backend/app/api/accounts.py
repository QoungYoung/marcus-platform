# -*- coding: utf-8 -*-
"""Accounts API endpoints — 模拟盘账户注册表列表。"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Optional

from app.database import SessionLocal
from app.models.paper_trade import PaperAccount, PaperAccountInfo

router = APIRouter(tags=["Accounts"])


class AccountSummary(BaseModel):
    account_id: str
    name: str
    module: str = ""
    initial_capital: float
    available_cash: float
    enabled: int = 1


@router.get("/accounts", response_model=List[AccountSummary])
def list_accounts():
    """列出所有启用账户（含 initial_capital、available_cash）。"""
    db = SessionLocal()
    try:
        accounts = db.query(PaperAccount).filter(PaperAccount.enabled == 1).order_by(PaperAccount.account_id).all()
        cash_map = {}
        for info in db.query(PaperAccountInfo).all():
            cash_map[info.account_id] = float(info.available_cash or 0)
        return [
            AccountSummary(
                account_id=a.account_id,
                name=a.name,
                module=a.module or "",
                initial_capital=float(a.initial_capital or 0),
                available_cash=cash_map.get(a.account_id, float(a.initial_capital or 0)),
                enabled=int(a.enabled or 1),
            )
            for a in accounts
        ]
    finally:
        db.close()
