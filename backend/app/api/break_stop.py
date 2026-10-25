# -*- coding: utf-8 -*-
"""破位止损 API（BreakStop v1 / P4）。

只读为主；`POST /break/run` 手动触发一次编排（默认 alert，不下单）。
"""
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.break_repository import BreakRepository
from app.services.break_stop_service import BreakStopService, mode as break_mode

router = APIRouter(prefix="/break", tags=["Break Stop"])


class RunReq(BaseModel):
    mode: Optional[str] = "alert"
    asof: Optional[str] = None
    live_quote: bool = False
    push_qq: bool = True


@router.get("/state")
def break_state(date: Optional[str] = Query(None, description="YYYYMMDD"),
                limit: int = Query(200, ge=1, le=1000)):
    repo = BreakRepository()
    return repo.latest(date)


@router.get("/events")
def break_events(date: Optional[str] = Query(None), symbol: Optional[str] = Query(None),
                 limit: int = Query(200, ge=1, le=1000)):
    return {"items": BreakRepository().list_events(date, symbol, limit)}


@router.get("/actions")
def break_actions(date: Optional[str] = Query(None), symbol: Optional[str] = Query(None),
                  limit: int = Query(200, ge=1, le=1000)):
    return {"items": BreakRepository().list_actions(date, symbol, limit)}


@router.get("/config")
def break_config():
    return {
        "enabled": os.getenv("WOLF_BREAK_STOP", "0"),
        "mode": break_mode(),
        "index_symbol": "000001.SH",
        "index_cap": os.getenv("WOLF_BREAK_INDEX_CAP", "0.50"),
        "index_cap_deep": os.getenv("WOLF_BREAK_INDEX_CAP_DEEP", "0.30"),
        "down_cap": os.getenv("WOLF_BREAK_DOWN_CAP", "0.30"),
        "down_default": os.getenv("WOLF_BREAK_DOWN_DEFAULT", "0.0"),
        "stock_exec": os.getenv("WOLF_BREAK_STOCK_EXEC", "0"),
        "vol_direct": os.getenv("WOLF_BREAK_VOL_DIRECT", "1.2"),
        "vol_shrink": os.getenv("WOLF_BREAK_VOL_SHRINK", "0.8"),
        "reclaim_days": os.getenv("WOLF_BREAK_RECLAIM_DAYS", "2"),
        "note": "execute_index 在 P5 回测通过且用户批准前不执行下单",
    }


@router.post("/run")
def break_run(req: RunReq = RunReq()):
    m = str(req.mode or "alert").lower()
    if m == "execute_index":
        raise HTTPException(status_code=409,
                            detail="execute_index 未启用：P5 回测通过且用户批准后才允许下单")
    if m not in ("alert", "off"):
        raise HTTPException(status_code=400, detail="mode must be alert|off|execute_index")
    try:
        out = BreakStopService().run(mode_=m, asof=req.asof,
                                     live_quote=bool(req.live_quote),
                                     push_qq=bool(req.push_qq))
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail="%s: %s" % (type(e).__name__, str(e)[:200]))
