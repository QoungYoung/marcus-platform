# -*- coding: utf-8 -*-
"""
加仓监控日志 API — 查询 PositionTierMonitor 持久化到 PostgreSQL 的检测结果。
"""
from datetime import datetime, date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc

from app.database import SessionLocal
from app.models.position_add_log import PositionAddMonitorLog

router = APIRouter(prefix="/monitor", tags=["Monitor Log"])


@router.get("/logs")
def list_monitor_logs(
    symbol: Optional[str] = Query(None, description="标的代码"),
    result: Optional[str] = Query(None, description="结果类型: HOLD/BLOCKED/EXECUTED/SKIPPED/OUTFLOW"),
    date_from: Optional[str] = Query(None, description="起始日期 YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
):
    """查询加仓监控日志列表（不含门控和技术详情 JSONB）。"""
    db = SessionLocal()
    try:
        q = db.query(PositionAddMonitorLog)

        if symbol:
            q = q.filter(PositionAddMonitorLog.symbol == symbol)
        if result:
            q = q.filter(PositionAddMonitorLog.result == result)
        if date_from:
            q = q.filter(PositionAddMonitorLog.timestamp >= datetime.fromisoformat(date_from))
        if date_to:
            q = q.filter(PositionAddMonitorLog.timestamp < datetime.fromisoformat(date_to + "T23:59:59"))

        total = q.count()
        rows = (
            q.order_by(desc(PositionAddMonitorLog.timestamp))
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        # 列表不返回 JSONB 大字段，减少 payload
        items = []
        for r in rows:
            items.append({
                'id': r.id,
                'timestamp': r.timestamp.isoformat() if r.timestamp else '',
                'symbol': r.symbol,
                'current_tier': r.current_tier,
                'target_tier': r.target_tier,
                'action': r.action,
                'result': r.result,
                'float_pnl_pct': r.float_pnl_pct,
                'current_price': r.current_price,
                'add_shares': r.add_shares,
                'block_reason': r.block_reason,
            })

        return {
            'items': items,
            'total': total,
            'page': page,
            'page_size': page_size,
        }
    finally:
        db.close()


@router.get("/logs/{log_id}")
def get_monitor_log_detail(log_id: int):
    """获取单条加仓监控日志详情（含门控详情和趋势指标 JSONB）。"""
    db = SessionLocal()
    try:
        row = db.query(PositionAddMonitorLog).filter(PositionAddMonitorLog.id == log_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="日志不存在")

        return {
            'id': row.id,
            'timestamp': row.timestamp.isoformat() if row.timestamp else '',
            'symbol': row.symbol,
            'current_tier': row.current_tier,
            'target_tier': row.target_tier,
            'action': row.action,
            'result': row.result,
            'float_pnl_pct': row.float_pnl_pct,
            'current_price': row.current_price,
            'add_shares': row.add_shares,
            'block_reason': row.block_reason,
            'gate_details': row.gate_details,
            'trend_details': row.trend_details,
        }
    finally:
        db.close()
