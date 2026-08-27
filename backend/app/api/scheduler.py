# -*- coding: utf-8 -*-
"""
Scheduler API endpoints - 任务调度管理

API/Worker 拆分后，本模块只读 worker 进程发布的状态快照（worker_status），
并把控制操作写入 worker_commands 由 worker 进程执行。
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.scheduler_service import scheduler_service
from app.services.worker_control import read_status, send_command

router = APIRouter(prefix="/scheduler", tags=["Scheduler"])


class TaskUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    schedule: Optional[dict] = None
    notifications: Optional[dict] = None


class TaskTriggerRequest(BaseModel):
    task_id: str


def _snapshot() -> dict:
    """读取 worker 状态快照；离线时返回空。"""
    st = read_status()
    if not st.get("online"):
        return {}
    return st.get("snapshot", {})


@router.get("/status")
def get_scheduler_status():
    """获取调度器状态（来自 worker 快照）"""
    snap = _snapshot()
    if not snap:
        return {"running": False, "online": False, "message": "worker 离线"}
    status = snap.get("scheduler") or {}
    status["online"] = True
    return status


@router.get("/tasks")
def get_tasks():
    """获取所有任务（来自 worker 快照）"""
    snap = _snapshot()
    return {
        "tasks": snap.get("tasks", []),
        "online": bool(snap),
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    """获取单个任务详情（来自 worker 快照）"""
    task = next((t for t in _snapshot().get("tasks", []) if t.get("id") == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/tasks/{task_id}/executions")
def get_task_executions(
    task_id: str,
    limit: int = Query(20, ge=1, le=100),
):
    """获取任务执行历史（直接读 worker 写入的 JSONL 日志）"""
    return {
        "executions": scheduler_service.read_executions_from_disk(task_id, limit),
        "timestamp": datetime.now().isoformat(),
    }


@router.post("/tasks/{task_id}/trigger")
def trigger_task(task_id: str):
    """手动触发任务（命令交由 worker 执行）"""
    return send_command("scheduler.trigger", {"task_id": task_id})


@router.post("/tasks/{task_id}/enable")
def enable_task(task_id: str):
    """启用任务"""
    return send_command("scheduler.enable", {"task_id": task_id})


@router.post("/tasks/{task_id}/disable")
def disable_task(task_id: str):
    """禁用任务"""
    return send_command("scheduler.disable", {"task_id": task_id})


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, updates: TaskUpdateRequest):
    """更新任务配置"""
    update_dict = updates.model_dump(exclude_none=True)
    return send_command("scheduler.update", {"task_id": task_id, "updates": update_dict})


@router.get("/next-runs")
def get_next_runs():
    """获取即将执行的任务（来自 worker 快照）"""
    snap = _snapshot()
    return {
        "runs": snap.get("next_runs", []),
        "online": bool(snap),
        "timestamp": datetime.now().isoformat(),
    }


@router.post("/reload")
def reload_config():
    """重新加载配置（命令交由 worker 执行）"""
    return send_command("scheduler.reload")


@router.post("/start")
def start_scheduler():
    """启动调度器（命令交由 worker 执行）"""
    return send_command("scheduler.start")


@router.get("/executions/{execution_id}/log")
def get_execution_log(execution_id: str):
    """获取执行详细日志"""
    log_path = scheduler_service.get_execution_log(execution_id)
    if not log_path:
        raise HTTPException(status_code=404, detail="Log not found")
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return {"success": True, "content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stop-loss-monitor")
def get_stop_loss_monitor_status():
    """获取止损监控器运行状态（来自 worker 快照）"""
    snap = _snapshot()
    status = snap.get("stop_loss_monitor") or {}
    if not snap:
        return {"success": False, "error": "worker 离线", "running": False, "timestamp": datetime.now().isoformat()}
    return {
        "success": True,
        **status,
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/stop-loss-monitor/distances")
def get_stop_loss_distances():
    """获取所有持仓到各止损线的距离（来自 worker 快照）"""
    snap = _snapshot()
    if not snap:
        return {"success": False, "error": "worker 离线", "positions": [], "timestamp": datetime.now().isoformat()}
    return {
        "success": True,
        "positions": snap.get("stop_loss_distances", []),
        "market_pct": None,  # 由调用方自行获取
        "timestamp": datetime.now().isoformat(),
    }


@router.post("/stop-loss-monitor/start")
def start_stop_loss_monitor():
    """启动止损监控器（命令交由 worker 执行）"""
    return send_command("monitor.stop_loss.start")


@router.post("/stop-loss-monitor/stop")
def stop_stop_loss_monitor():
    """停止止损监控器（命令交由 worker 执行）"""
    return send_command("monitor.stop_loss.stop")


@router.post("/tier-monitor/start")
def start_tier_monitor_endpoint():
    """启动加仓层级监控器（命令交由 worker 执行）"""
    return send_command("monitor.tier.start")


@router.post("/tier-monitor/stop")
def stop_tier_monitor_endpoint():
    """停止加仓层级监控器（命令交由 worker 执行）"""
    return send_command("monitor.tier.stop")


@router.post("/stop")
def stop_scheduler():
    """停止调度器（命令交由 worker 执行）"""
    return send_command("scheduler.stop")
