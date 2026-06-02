# -*- coding: utf-8 -*-
"""
Scheduler API endpoints - 任务调度管理
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.scheduler_service import scheduler_service

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


@router.post("/stop")
async def stop_scheduler():
    """停止调度器"""
    scheduler_service.stop()
    return {"success": True, "message": "Scheduler stopped"}
