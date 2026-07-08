# -*- coding: utf-8 -*-
"""
Scheduler API endpoints - 任务调度管理
"""
from datetime import datetime
from typing import Optional
import sys

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.scheduler_service import scheduler_service
from app.services.stop_loss_monitor import get_monitor_status, get_position_distances, start_monitor, stop_monitor, get_stop_loss_monitor
from app.services.position_tier_monitor import start_tier_monitor, stop_tier_monitor, get_position_tier_monitor

router = APIRouter(prefix="/scheduler", tags=["Scheduler"])


class TaskUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    schedule: Optional[dict] = None
    notifications: Optional[dict] = None


class TaskTriggerRequest(BaseModel):
    task_id: str


@router.get("/status")
async def get_scheduler_status():
    """获取调度器状态"""
    return scheduler_service.get_scheduler_status()


@router.get("/tasks")
async def get_tasks():
    """获取所有任务"""
    return {
        "tasks": scheduler_service.get_tasks(),
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """获取单个任务详情"""
    task = scheduler_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/tasks/{task_id}/executions")
async def get_task_executions(
    task_id: str,
    limit: int = Query(20, ge=1, le=100)
):
    """获取任务执行历史"""
    return {
        "executions": scheduler_service.get_task_executions(task_id, limit),
        "timestamp": datetime.now().isoformat(),
    }


@router.post("/tasks/{task_id}/trigger")
async def trigger_task(task_id: str):
    """手动触发任务"""
    result = scheduler_service.trigger_task(task_id)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error'))
    return result


@router.post("/tasks/{task_id}/enable")
async def enable_task(task_id: str):
    """启用任务"""
    result = scheduler_service.enable_task(task_id)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error'))
    return result


@router.post("/tasks/{task_id}/disable")
async def disable_task(task_id: str):
    """禁用任务"""
    result = scheduler_service.disable_task(task_id)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error'))
    return result


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, updates: TaskUpdateRequest):
    """更新任务配置"""
    update_dict = updates.model_dump(exclude_none=True)
    result = scheduler_service.update_task(task_id, update_dict)
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error'))
    return result


@router.get("/next-runs")
async def get_next_runs():
    """获取即将执行的任务"""
    return {
        "runs": scheduler_service.get_next_runs(),
        "timestamp": datetime.now().isoformat(),
    }


@router.post("/reload")
async def reload_config():
    """重新加载配置"""
    scheduler_service.reload_config()
    return {"success": True, "message": "Configuration reloaded"}


@router.post("/start")
async def start_scheduler():
    """启动调度器"""
    scheduler_service.start()
    return {"success": True, "message": "Scheduler started"}


@router.get("/executions/{execution_id}/log")
async def get_execution_log(execution_id: str):
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
async def get_stop_loss_monitor_status():
    """获取实时止损监控器运行状态（含持仓止损距离）"""
    try:
        status = get_monitor_status()
        return {
            "success": True,
            **status,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        }


@router.get("/stop-loss-monitor/distances")
async def get_stop_loss_distances():
    """获取所有持仓到各止损线的距离"""
    try:
        distances = get_position_distances()
        return {
            "success": True,
            "positions": distances,
            "market_pct": None,  # 由调用方自行获取
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        }


@router.post("/stop-loss-monitor/start")
async def start_stop_loss_monitor():
    """启动止损监控器（自动关联 MarcusVNPyExecutor）"""
    try:
        # 尝试导入并创建 executor
        executor = None
        try:
            from app.core.trading.marcus_trade import MarcusVNPyExecutor
            executor = MarcusVNPyExecutor()
            print("[StopLoss] ✅ 已创建 MarcusVNPyExecutor", file=sys.stderr)
        except Exception as e:
            print(f"[StopLoss] ⚠️ 无法创建 executor: {e}", file=sys.stderr)

        ok = start_monitor(executor=executor)
        monitor = get_stop_loss_monitor()
        return {
            "success": ok,
            "message": "止损监控已启动" if ok else "启动失败",
            "running": monitor.running,
            "thread_alive": monitor.thread.is_alive() if monitor.thread else False,
            "has_executor": monitor.executor is not None,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        }


@router.post("/stop-loss-monitor/stop")
async def stop_stop_loss_monitor():
    """停止止损监控器"""
    try:
        stop_monitor()
        return {
            "success": True,
            "message": "止损监控已停止",
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        }


@router.post("/tier-monitor/start")
async def start_tier_monitor_endpoint():
    """启动加仓层级监控器（自动关联 MarcusVNPyExecutor）"""
    try:
        executor = None
        try:
            from app.core.trading.marcus_trade import MarcusVNPyExecutor
            executor = MarcusVNPyExecutor()
            print("[TierMonitor] ✅ 已创建 MarcusVNPyExecutor", file=sys.stderr)
        except Exception as e:
            print(f"[TierMonitor] ⚠️ 无法创建 executor: {e}", file=sys.stderr)

        ok = start_tier_monitor(executor=executor)
        monitor = get_position_tier_monitor()
        return {
            "success": ok,
            "message": "加仓层级监控已启动" if ok else "启动失败",
            "running": monitor.is_running(),
            "has_executor": monitor.executor is not None,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        }


@router.post("/tier-monitor/stop")
async def stop_tier_monitor_endpoint():
    """停止加仓层级监控器"""
    try:
        stop_tier_monitor()
        return {
            "success": True,
            "message": "加仓层级监控已停止",
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        }


@router.post("/stop")
async def stop_scheduler():
    """停止调度器"""
    scheduler_service.stop()
    return {"success": True, "message": "Scheduler stopped"}
