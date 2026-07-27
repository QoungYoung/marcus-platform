# -*- coding: utf-8 -*-
"""黄金坑预测 API 端点 v2 — 逐宽基指数追踪 + 三重确认。"""

import logging
from typing import Optional
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.services.golden_pit_service import GoldenPitService, ArkvolServiceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/golden-pit", tags=["golden-pit"])

_service = None


def _get_service() -> GoldenPitService:
    global _service
    if _service is None:
        _service = GoldenPitService()
    return _service


# ── v2 endpoints ──

@router.get("/status")
async def get_golden_pit_status():
    """获取完整的 per-index 黄金坑状态 + 窗口信息 + 三重确认 + 预测。"""
    try:
        result = _get_service().get_status()
        return {"code": 0, "data": result}
    except ArkvolServiceError as exc:
        return JSONResponse(
            status_code=502,
            content={"code": 1, "msg": str(exc), "data": None},
        )


@router.get("/history")
async def get_golden_pit_history(
    index: str = Query("all", description="基金代码, 'all' 返回全部A股宽基"),
    days: int = Query(60, ge=1, le=365, description="返回天数"),
):
    """获取历史贪婪值趋势数据，用于前端折线图。"""
    try:
        result = _get_service().get_history(index=index, days=days)
        return {"code": 0, "data": result}
    except ArkvolServiceError as exc:
        return JSONResponse(
            status_code=502,
            content={"code": 1, "msg": str(exc), "data": None},
        )


@router.get("/snapshots")
async def get_golden_pit_snapshots(
    days: int = Query(30, ge=1, le=365, description="返回天数"),
):
    """从数据库读取历史快照。"""
    try:
        result = _get_service().get_snapshots(days=days)
        return {"code": 0, "data": result}
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"code": 1, "msg": str(exc), "data": None},
        )


# ── v1 backward-compat endpoints ──

@router.get("/score")
async def get_score():
    """[v1 兼容] 获取综合评分。新代码请使用 /status。"""
    try:
        result = _get_service().get_score()
        return {"code": 0, "data": result}
    except ArkvolServiceError as exc:
        return JSONResponse(
            status_code=502,
            content={"code": 1, "msg": str(exc), "data": None},
        )


@router.get("/factors")
async def get_factors():
    """[v1 兼容] 获取因子明细。新代码请使用 /status。"""
    try:
        result = _get_service().get_factors()
        return {"code": 0, "data": result}
    except ArkvolServiceError as exc:
        return JSONResponse(
            status_code=502,
            content={"code": 1, "msg": str(exc), "data": None},
        )
