# -*- coding: utf-8 -*-
"""跨窗口候选池 API — 查看/添加/删除/刷新候选标的"""

import logging
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pool", tags=["Candidate Pool"])


class AddCandidateRequest(BaseModel):
    symbol: str


class CandidateOut(BaseModel):
    symbol: str
    name: str
    status: str
    added_at: str
    added_trade_day: str
    expire_trade_day: str
    reject_reasons: list[str] = []
    checks_count: int = 0
    chain_name: str = ""
    chain_role: str = ""


class PoolListResponse(BaseModel):
    candidates: list[CandidateOut]
    total: int
    ready_count: int
    waiting_count: int
    updated_at: str


class RefreshResponse(BaseModel):
    newly_ready: list[str]
    ready_count: int
    waiting_count: int


def _to_out(entry: dict) -> CandidateOut:
    return CandidateOut(
        symbol=entry.get("symbol", ""),
        name=entry.get("name", ""),
        status=entry.get("status", ""),
        added_at=entry.get("added_at", ""),
        added_trade_day=entry.get("added_trade_day", ""),
        expire_trade_day=entry.get("expire_trade_day", ""),
        reject_reasons=entry.get("reject_reasons", []),
        checks_count=entry.get("checks_count", 0),
        chain_name=entry.get("chain_name", ""),
        chain_role=entry.get("chain_role", ""),
    )


@router.get("/candidates", response_model=PoolListResponse)
async def list_candidates(
    status: str = Query(None, description="筛选: waiting / ready / expired / promoted"),
):
    """列出候选池中的所有标的"""
    from app.services.candidate_pool import get_candidate_pool

    pool = get_candidate_pool()
    all_candidates = pool._data["candidates"]

    if status:
        all_candidates = [e for e in all_candidates if e.get("status") == status]

    ready_count = len(pool.get_ready())
    waiting_count = len(pool.get_waiting())

    return PoolListResponse(
        candidates=[_to_out(e) for e in all_candidates],
        total=len(all_candidates),
        ready_count=ready_count,
        waiting_count=waiting_count,
        updated_at=pool._data.get("updated_at", ""),
    )


@router.post("/candidates")
async def add_candidate(req: AddCandidateRequest):
    """手动添加候选标的到池中"""
    from app.services.candidate_pool import get_candidate_pool
    from app.api.indicator import _to_xueqiu_symbol

    pool = get_candidate_pool()
    sym = _to_xueqiu_symbol(req.symbol)

    ok = pool.add_manual(sym, "")
    if not ok:
        raise HTTPException(status_code=409, detail=f"{sym} 已在候选池中")
    return {"symbol": sym, "status": "added"}


@router.delete("/candidates/{symbol:path}")
async def remove_candidate(symbol: str):
    """从候选池中删除标的"""
    from app.services.candidate_pool import get_candidate_pool
    from app.api.indicator import _to_xueqiu_symbol

    pool = get_candidate_pool()
    sym = _to_xueqiu_symbol(symbol)

    pool.remove(sym)
    return {"symbol": sym, "status": "removed"}


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_pool():
    """手动触发候选池刷新（重跑 check_entry_filters）"""
    from app.services.candidate_pool import get_candidate_pool

    pool = get_candidate_pool()
    pool.expire_stale()
    newly_ready = pool.refresh_all_sync()

    return RefreshResponse(
        newly_ready=newly_ready,
        ready_count=len(pool.get_ready()),
        waiting_count=len(pool.get_waiting()),
    )
