# -*- coding: utf-8 -*-
"""黄金坑预测 API 端点。"""

import logging
from fastapi import APIRouter
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


@router.get("/score")
async def get_score():
    """获取当前黄金坑综合评分及全部因子明细。"""
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
    """仅获取各因子明细（不含综合评分）。"""
    try:
        result = _get_service().get_factors()
        return {"code": 0, "data": result}
    except ArkvolServiceError as exc:
        return JSONResponse(
            status_code=502,
            content={"code": 1, "msg": str(exc), "data": None},
        )
