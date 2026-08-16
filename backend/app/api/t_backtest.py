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
    """创建回测任务（支持单标的与组合模式）。

    body: {
        symbol: "SH600519" | "600519"（组合模式可省略/combined）,
        symbols: ["600519", "000001", ...]（组合模式候选列表，空则用做T候选池）,
        build_mode: true（组合建仓模拟）| false,
        build_limit_ratio: 0.55（建仓资金上限比例）,
        start_date/end_date/conditions/init_shares/init_price/net_asset/review_mode
    }
    """
    symbols = body.get("symbols") or []
    build_mode = bool(body.get("build_mode", False))
    # 自动选股：select_source=pool（做T候选池）/scan（全市场扫描）；symbols 为空且选择自动时启用
    select_source = str(body.get("select_source") or ("manual" if symbols else "pool"))
    select_limit = int(body.get("select_limit") or 10)
    symbol = body.get("symbol") or ("combined" if (symbols or build_mode or select_source in ("pool", "scan")) else "")
    if not symbol:
        raise HTTPException(status_code=400, detail="缺少 symbol（单标的）或 symbols/select_source（组合）")
    conditions = body.get("conditions") or []
    if not isinstance(conditions, list):
        raise HTTPException(status_code=400, detail="conditions 必须为数组")
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
        symbols=symbols,
        build_mode=build_mode,
        build_limit_ratio=float(body.get("build_limit_ratio", 0.55)),
        select_source=select_source,
        select_limit=select_limit,
        rolling_build=bool(body.get("rolling_build", False)),
        rolling_scan=bool(body.get("rolling_scan", False)),
        relax_mode=bool(body.get("relax_mode", False)),
    )
    if not task_id:
        raise HTTPException(status_code=500, detail="任务创建失败")
    mode = "combined" if (symbols or build_mode or select_source in ("pool", "scan")) else "single"
    return {"success": True, "task_id": task_id, "status": "pending", "mode": mode}


@router.get("/candidates")
def t_backtest_candidates(limit: int = 10):
    """做T建仓候选池查询（供页面"自动候选池"选择，含可T质量分）。"""
    try:
        from app.services.t_build import scan_t_candidates
        cands = scan_t_candidates(limit=limit, source="pool")
        out = [{
            "symbol": c.get("symbol"), "score": c.get("score"),
            "pass_gate": c.get("pass_gate"), "reasons": c.get("reasons"),
            "trend": (c.get("trend") or {}).get("note", ""),
        } for c in cands]
        return {"candidates": out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"候选池查询失败: {e}")


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
            # 明细表按 task_id 删除
            for tbl in ("t_backtest_events", "t_backtest_trades",
                        "t_backtest_equity_snapshots", "t_backtest_metrics"):
                db.execute(text(f"DELETE FROM {tbl} WHERE task_id = :id"), {"id": task_id})
            # t_backtest_tasks 主键是 id（不是 task_id），单独按 id 删除
            db.execute(text("DELETE FROM t_backtest_tasks WHERE id = :id"), {"id": task_id})
            db.commit()
        finally:
            db.close()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")


@router.get("/{task_id}/report")
def t_backtest_report(task_id: int):
    """指标报告 + 口径差异声明（组合模式含 build_decisions/per_symbol 明细）+ 权益曲线。"""
    task = runner.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 #{task_id} 不存在")
    metrics = runner.get_metrics(task_id)
    if not metrics:
        raise HTTPException(status_code=409, detail="任务尚未完成或报告未生成")
    # 权益曲线（组合/单标的共用 t_backtest_equity_snapshots）
    equity_curve = []
    try:
        from sqlalchemy import text as _text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            rows = db.execute(_text(
                "SELECT trade_date, total_asset FROM t_backtest_equity_snapshots "
                "WHERE task_id = :id ORDER BY trade_date"
            ), {"id": task_id}).mappings().all()
            equity_curve = [dict(r) for r in rows]
        finally:
            db.close()
    except Exception:
        pass
    return {
        "task_id": task_id, "symbol": task.get("symbol"),
        "status": task.get("status"), **metrics,
        "equity_curve": equity_curve,
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
                    # data_json 为 JSONB：SQLAlchemy 已反序列化为 dict/list，兼容 str 双保险
                    raw = r["data_json"]
                    data = raw if isinstance(raw, (dict, list)) else json.loads(raw or "{}")
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
