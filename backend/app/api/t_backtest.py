# -*- coding: utf-8 -*-
"""做T回测 · 任务管理 API（/api/v1/t/backtest）。

创建/启动/取消/查询/报告。任务执行由 worker 轮询 pending 领取（重活不阻塞 API）；
同任务并发启动由 claim_pending_task 的 FOR UPDATE SKIP LOCKED 原子领取保证。
"""
import json
from typing import List, Optional

from fastapi import APIRouter, HTTPException

from app.services import t_backtest_runner as runner

router = APIRouter(prefix="/t/backtest", tags=["t-backtest"])


@router.post("")
def t_backtest_create(body: dict):
    """创建回测任务。

    body: {
        symbol: "SH600519" | "600519",
        start_date: "2026-07-01",
        end_date: "2026-08-14",
        conditions: [ {trigger_kind/target_price/vol_ratio_thresh/expression/...}, ... ],
        init_shares: 1000 (默认), init_price: float (默认回测首日价),
        net_asset: 200000 (默认), review_mode: "llm"|"rule" (默认 llm)
    }
    """
    symbol = body.get("symbol")
    if not symbol:
        raise HTTPException(status_code=400, detail="缺少 symbol")
    conditions = body.get("conditions") or []
    if not isinstance(conditions, list) or len(conditions) == 0:
        raise HTTPException(status_code=400, detail="conditions 必须为非空数组（至少一条监控条件）")
    start = body.get("start_date") or ""
    end = body.get("end_date") or ""
    if not start or not end:
        raise HTTPException(status_code=400, detail="缺少 start_date / end_date")
    review_mode = body.get("review_mode", "llm")
    if review_mode not in ("llm", "rule"):
        raise HTTPException(status_code=400, detail="review_mode 必须为 llm 或 rule")

    task_id = runner.create_task(
        symbol=symbol, start_date=start, end_date=end, conditions=conditions,
        init_shares=int(body.get("init_shares", 1000)),
        init_price=body.get("init_price"),
        net_asset=float(body.get("net_asset", 200000.0)),
        review_mode=review_mode,
    )
    if not task_id:
        raise HTTPException(status_code=500, detail="任务创建失败")
    return {"success": True, "task_id": task_id, "status": "pending"}


@router.get("/tasks")
def t_backtest_list(limit: int = 50):
    """回测任务列表（倒序）。"""
    tasks = runner.list_tasks(limit=limit)
    for t in tasks:
        try:
            t["conditions_json"] = json.loads(t.get("conditions_json") or "[]")
        except (ValueError, TypeError):
            t["conditions_json"] = []
    return {"tasks": tasks}


@router.get("/{task_id}")
def t_backtest_detail(task_id: int):
    """任务详情：基本信息 + 指标报告 + 权益曲线。"""
    task = runner.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 #{task_id} 不存在")
    try:
        task["conditions_json"] = json.loads(task.get("conditions_json") or "[]")
    except (ValueError, TypeError):
        task["conditions_json"] = []
    metrics = runner.get_metrics(task_id)
    return {"task": task, "metrics": metrics}


@router.post("/{task_id}/start")
def t_backtest_start(task_id: int):
    """启动/重启任务：pending 保持；cancelled/failed 重新置 pending（worker 领取执行）。"""
    task = runner.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 #{task_id} 不存在")
    if task.get("status") in ("running", "completed"):
        return {"success": False, "status": task.get("status"),
                "detail": "任务已在执行或已完成（并发启动拒绝）"}
    ok = runner.update_task_status(task_id, "pending")
    return {"success": ok, "status": "pending"}


@router.post("/{task_id}/cancel")
def t_backtest_cancel(task_id: int):
    """取消任务（仅 pending/running）。"""
    task = runner.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 #{task_id} 不存在")
    if task.get("status") in ("completed", "cancelled"):
        return {"success": True, "status": task.get("status"), "detail": "无操作"}
    ok = runner.cancel_task(task_id)
    return {"success": ok, "status": "cancelled"}


@router.delete("/{task_id}")
def t_backtest_delete(task_id: int):
    """删除任务记录（仅终态可删）。"""
    task = runner.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 #{task_id} 不存在")
    if task.get("status") in ("pending", "running"):
        raise HTTPException(status_code=400, detail="任务未结束，不可删除")
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            for tbl in ("t_backtest_events", "t_backtest_trades",
                        "t_backtest_equity_snapshots", "t_backtest_metrics",
                        "t_backtest_tasks"):
                db.execute(text(f"DELETE FROM {tbl} WHERE task_id = :id"), {"id": task_id})
                if tbl == "t_backtest_tasks":
                    db.execute(text("DELETE FROM t_backtest_tasks WHERE id = :id"), {"id": task_id})
            db.commit()
        finally:
            db.close()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")


@router.get("/{task_id}/report")
def t_backtest_report(task_id: int):
    """指标报告 + 口径差异声明。"""
    task = runner.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 #{task_id} 不存在")
    metrics = runner.get_metrics(task_id)
    if not metrics:
        raise HTTPException(status_code=409, detail="任务尚未完成或报告未生成")
    return {
        "task_id": task_id, "symbol": task.get("symbol"),
        "status": task.get("status"), **metrics,
    }


@router.get("/{task_id}/events")
def t_backtest_events(task_id: int, limit: int = 500):
    """回测事件流（触发/复核/拦截/缺口）。"""
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            rows = db.execute(text(
                "SELECT event_type, trade_day, bar_time, data_json FROM t_backtest_events "
                "WHERE task_id = :id ORDER BY id LIMIT :lim"
            ), {"id": task_id, "lim": limit}).mappings().all()
            out = []
            for r in rows:
                try:
                    data = json.loads(r["data_json"] or "{}")
                except (ValueError, TypeError):
                    data = {}
                out.append({
                    "event_type": r["event_type"], "trade_day": r["trade_day"],
                    "bar_time": r["bar_time"], "data": data,
                })
            return {"events": out}
        finally:
            db.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"事件查询失败: {e}")
