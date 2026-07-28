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


# ── DCA ETF 配置 & 执行日志 endpoints ──

@router.get("/etf-configs")
async def get_etf_configs():
    """获取所有黄金坑 ETF 定投配置。"""
    try:
        from app.database import SessionLocal
        from app.models.golden_pit_etf_config import GoldenPitETFConfig

        db = SessionLocal()
        try:
            rows = (
                db.query(GoldenPitETFConfig)
                .order_by(GoldenPitETFConfig.priority)
                .all()
            )
            configs = [
                {
                    "id": r.id,
                    "fund_code": r.fund_code,
                    "index_name": r.index_name,
                    "etf_code": r.etf_code,
                    "etf_name": r.etf_name,
                    "priority": r.priority,
                    "strategy": r.strategy,
                    "daily_amount": r.daily_amount,
                    "max_total_amount": r.max_total_amount,
                    "max_position_pct": r.max_position_pct,
                    "require_absolute_threshold": r.require_absolute_threshold,
                    "min_days_in_pit": r.min_days_in_pit,
                    "skip_if_already_holding": r.skip_if_already_holding,
                    "enabled": r.enabled,
                    "notes": r.notes,
                }
                for r in rows
            ]
            return {"code": 0, "data": configs}
        finally:
            db.close()
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"code": 1, "msg": str(exc), "data": None},
        )


@router.put("/etf-configs/{fund_code}")
async def update_etf_config(fund_code: str, body: dict):
    """更新指定 ETF 的定投配置（enabled / strategy / daily_amount / max_total_amount）。"""
    try:
        from app.database import SessionLocal
        from app.models.golden_pit_etf_config import GoldenPitETFConfig

        db = SessionLocal()
        try:
            row = (
                db.query(GoldenPitETFConfig)
                .filter(GoldenPitETFConfig.fund_code == fund_code)
                .first()
            )
            if not row:
                return JSONResponse(
                    status_code=404,
                    content={"code": 1, "msg": f"未找到 {fund_code} 的配置", "data": None},
                )

            allowed_fields = [
                "enabled", "strategy", "daily_amount", "max_total_amount",
                "max_position_pct", "require_absolute_threshold",
                "min_days_in_pit", "skip_if_already_holding",
            ]
            for key in allowed_fields:
                if key in body:
                    setattr(row, key, body[key])

            db.commit()
            return {"code": 0, "data": {"fund_code": fund_code, "updated": list(body.keys())}}
        finally:
            db.close()
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"code": 1, "msg": str(exc), "data": None},
        )


@router.get("/dca/logs")
async def get_dca_logs(
    days: int = Query(30, ge=1, le=365, description="返回天数"),
    fund_code: str = Query("", description="筛选基金代码，空=全部"),
):
    """获取 DCA 执行日志。"""
    try:
        from datetime import timedelta
        from app.database import SessionLocal
        from app.models.golden_pit_dca_log import GoldenPitDCALog

        cutoff = (__import__("datetime").datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        db = SessionLocal()
        try:
            q = db.query(GoldenPitDCALog).filter(GoldenPitDCALog.created_at >= cutoff)
            if fund_code:
                q = q.filter(GoldenPitDCALog.fund_code == fund_code)
            rows = q.order_by(GoldenPitDCALog.created_at.desc()).all()

            logs = [
                {
                    "id": r.id,
                    "fund_code": r.fund_code,
                    "window_start": r.window_start,
                    "buy_day": r.buy_day,
                    "etf_code": r.etf_code,
                    "amount": r.amount,
                    "strategy": r.strategy,
                    "order_id": r.order_id,
                    "status": r.status,
                    "created_at": r.created_at,
                }
                for r in rows
            ]
            return {"code": 0, "data": logs}
        finally:
            db.close()
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"code": 1, "msg": str(exc), "data": None},
        )


@router.get("/dca/status")
async def get_dca_status():
    """获取当前 DCA 执行状态概览（本窗口已执行/待执行/累计金额）。"""
    try:
        from app.database import SessionLocal
        from app.models.golden_pit_etf_config import GoldenPitETFConfig
        from app.models.golden_pit_dca_log import GoldenPitDCALog

        status_data = _get_service().get_status()
        window = status_data.get("golden_pit_window", {})
        indices = status_data.get("indices", [])
        current_day = window.get("current_day", 0) if window.get("active") else 0
        window_start = window.get("start_date", "")

        db = SessionLocal()
        try:
            configs = (
                db.query(GoldenPitETFConfig)
                .filter(GoldenPitETFConfig.enabled == True)
                .order_by(GoldenPitETFConfig.priority)
                .all()
            )

            from app.services.golden_pit_dca_service import _resonance_multiplier

            result = {
                "as_of": status_data.get("as_of", ""),
                "window_active": window.get("active", False),
                "window_phase": window.get("phase", "idle"),
                "current_day": current_day,
                "window_start": window_start,
                "pit_count": window.get("pit_count", 0),
                "turning_count": window.get("turning_count", 0),
                "resonance_multiplier": _resonance_multiplier(indices),
                "global_macro": status_data.get("global_macro", {}),
                "etfs": [],
            }

            for cfg in configs:
                # 找对应指数的状态
                idx = next((i for i in indices if i["fund_code"] == cfg.fund_code), None)
                idx_status = idx["status"] if idx else "normal"

                # 统计本窗口执行情况
                executed = (
                    db.query(GoldenPitDCALog)
                    .filter(
                        GoldenPitDCALog.fund_code == cfg.fund_code,
                        GoldenPitDCALog.window_start == window_start,
                        GoldenPitDCALog.status.in_(("filled", "notified")),
                    )
                    .all()
                )
                executed_days = [e.buy_day for e in executed]
                total_invested = sum(e.amount for e in executed)

                # 计算策略下的总定投日
                strategy = cfg.strategy
                from app.services.golden_pit_dca_service import _strategy_weights
                weights = _strategy_weights(strategy)
                planned_days = [d + 1 for d, w in enumerate(weights) if w > 0]
                pending_days = [d for d in planned_days if d <= current_day and d not in executed_days]

                result["etfs"].append({
                    "fund_code": cfg.fund_code,
                    "index_name": cfg.index_name,
                    "etf_code": cfg.etf_code,
                    "status": idx_status,
                    "strategy": strategy,
                    "daily_amount": cfg.daily_amount,
                    "max_total_amount": cfg.max_total_amount,
                    "total_invested": round(total_invested, 2),
                    "remaining": round(max(0, cfg.max_total_amount - total_invested), 2),
                    "executed_days": executed_days,
                    "pending_days": pending_days,
                    "planned_days": planned_days,
                    "enabled": cfg.enabled,
                })

            return {"code": 0, "data": result}
        finally:
            db.close()
    except Exception as exc:
        return JSONResponse(
            status_code=500,
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
