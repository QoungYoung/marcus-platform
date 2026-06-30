# -*- coding: utf-8 -*-
"""长期观察候选池 API — 增删查改 + 监控器状态"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lt-pool", tags=["Long-Term Pool"])


# ── 请求/响应模型 ──

class AddLTCandidateRequest(BaseModel):
    symbol: str
    name: str = ""
    chain_name: str = ""
    chain_role: str = ""
    notes: str = ""


class UpdateLTCandidateRequest(BaseModel):
    notes: Optional[str] = None
    chain_name: Optional[str] = None
    chain_role: Optional[str] = None


class LTCandidateOut(BaseModel):
    id: int
    symbol: str
    name: str
    status: str
    chain_name: str
    chain_role: str
    notes: str
    added_at: str
    promoted_at: Optional[str] = None
    last_checked_at: Optional[str] = None
    last_grade: str = ""
    checks_count: int = 0


class LTPoolListResponse(BaseModel):
    candidates: list[LTCandidateOut]
    total: int
    active_count: int
    promoted_count: int


# ── Symbol 归一化 ──

def _normalize_lt_symbol(raw: str) -> str:
    """输入归一化为 paper engine 格式 (SZ301566 / SH600519)。

    兼容格式: 301566 / 301566.SZ / SZ301566 / 600519 / 600519.SH / SH600519
    """
    raw = raw.strip().upper()
    # "301566.SZ" → "SZ301566"
    if '.' in raw:
        code, exchange = raw.split('.', 1)
        if code.isdigit() and len(code) == 6 and exchange in ('SH', 'SZ', 'BJ'):
            return f"{exchange}{code}"
    # 已有前缀
    if raw.startswith(("SH", "SZ", "BJ")):
        return raw
    # 纯数字 → 推断前缀
    if raw.isdigit() and len(raw) == 6:
        prefix = raw[:3]
        if "000" <= prefix <= "004" or "300" <= prefix <= "301":
            return f"SZ{raw}"
        elif "600" <= prefix <= "605" or prefix == "688":
            return f"SH{raw}"
    return raw


# ── 输出转换 ──

def _to_out(entry: dict) -> LTCandidateOut:
    return LTCandidateOut(
        id=entry.get("id", 0),
        symbol=entry.get("symbol", ""),
        name=entry.get("name", ""),
        status=entry.get("status", ""),
        chain_name=entry.get("chain_name", ""),
        chain_role=entry.get("chain_role", ""),
        notes=entry.get("notes", ""),
        added_at=entry.get("added_at", ""),
        promoted_at=entry.get("promoted_at"),
        last_checked_at=entry.get("last_checked_at"),
        last_grade=entry.get("last_grade", ""),
        checks_count=entry.get("checks_count", 0),
    )


# ── 端点 ──

@router.get("/candidates", response_model=LTPoolListResponse)
async def list_candidates(
    status: Optional[str] = Query(None, description="筛选状态: active / promoted")
):
    """列出长期候选池中的标的"""
    from app.services.long_term_pool import get_long_term_pool

    pool = get_long_term_pool()
    if status == "promoted":
        candidates = pool.get_promoted()
    else:
        candidates = pool.get_all() if status is None else pool.get_active()
    active = pool.get_active()

    return LTPoolListResponse(
        candidates=[_to_out(c) for c in candidates],
        total=len(candidates),
        active_count=len(active),
        promoted_count=len(candidates) - len(active) if status is None else (
            len(candidates) if status == "promoted" else 0
        ),
    )


@router.post("/candidates")
async def add_candidate(req: AddLTCandidateRequest):
    """添加候选标的到长期观察池"""
    from app.services.long_term_pool import get_long_term_pool

    symbol = _normalize_lt_symbol(req.symbol)
    pool = get_long_term_pool()

    existing = pool.get_by_symbol(symbol)
    if existing:
        raise HTTPException(status_code=409, detail=f"{symbol} 已在长期候选池中")

    ok = pool.add(
        symbol=symbol,
        name=req.name,
        chain_name=req.chain_name,
        chain_role=req.chain_role,
        notes=req.notes,
    )
    if not ok:
        raise HTTPException(status_code=500, detail=f"添加 {symbol} 失败")
    return {"symbol": symbol, "status": "added"}


@router.delete("/candidates/{symbol:path}")
async def remove_candidate(symbol: str):
    """从长期候选池中删除标的"""
    from app.services.long_term_pool import get_long_term_pool

    symbol = _normalize_lt_symbol(symbol)
    pool = get_long_term_pool()

    ok = pool.remove(symbol)
    if not ok:
        raise HTTPException(status_code=404, detail=f"{symbol} 不在长期候选池中")
    return {"symbol": symbol, "status": "removed"}


@router.put("/candidates/{symbol:path}")
async def update_candidate(symbol: str, req: UpdateLTCandidateRequest):
    """更新候选标的的元数据（备注、产业链名、角色）"""
    from app.services.long_term_pool import get_long_term_pool

    symbol = _normalize_lt_symbol(symbol)
    pool = get_long_term_pool()

    ok = pool.update_meta(
        symbol=symbol,
        notes=req.notes,
        chain_name=req.chain_name,
        chain_role=req.chain_role,
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"{symbol} 不在长期候选池中")
    return {"symbol": symbol, "status": "updated"}


@router.get("/monitor/status")
async def get_monitor_status():
    """获取长期候选池监控器状态"""
    from app.services.long_term_pool_monitor import get_lt_pool_monitor_status

    status = get_lt_pool_monitor_status()
    return {
        "monitor": status,
        "timestamp": datetime.now().isoformat(),
    }
