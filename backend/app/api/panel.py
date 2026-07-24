# -*- coding: utf-8 -*-
"""
专家组群聊讨论 API — 前端触发 reflect panel discussion 的中转端点。
"""
import json
import logging
import os
import urllib.request
import ssl
import httpx
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["panel"])


class PanelRequest(BaseModel):
    message: str = Field(..., min_length=1, description="反思任务描述")
    session_id: Optional[str] = Field(None, description="会话ID，用于继续已有讨论")
    history_messages: Optional[List[Dict[str, Any]]] = Field(None, description="历史消息列表，用于上下文恢复")


class PanelResponse(BaseModel):
    reply: str
    session_id: str
    mode: str = "reflect"
    elapsed_ms: int


class ReflectSessionSummary(BaseModel):
    id: str
    start_date: str
    end_date: str
    stance: str
    position_limit: int
    created_at: str
    reason: str = ""


class ReflectSessionDetail(BaseModel):
    id: str
    start_date: str
    end_date: str
    stance: str
    position_limit: int
    created_at: str
    reason: str
    report: str


def _get_reflect_logs_dir() -> Path:
    """获取每周反思日志存储目录"""
    settings = get_settings()
    return settings.workspace_path / "memory" / "weekly-reflect-logs"


def _parse_reflect_file(filepath: Path) -> Optional[dict]:
    """解析单个 reflect JSON 文件，返回摘要信息；解析失败返回 None"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # 从文件名提取日期范围: {start}_to_{end}-reflect.json
        filename = filepath.stem  # e.g. "2026-07-20_to_2026-07-24-reflect"
        date_part = filename.replace('-reflect', '')
        parts = date_part.split('_to_')
        start_date = parts[0] if len(parts) >= 1 else ''
        end_date = parts[1] if len(parts) >= 2 else ''
        return {
            'id': date_part,
            'start_date': data.get('date_range', {}).get('start', start_date),
            'end_date': data.get('date_range', {}).get('end', end_date),
            'stance': data.get('stance', 'yellow'),
            'position_limit': data.get('position_limit', 60),
            'created_at': data.get('created_at', ''),
            'reason': data.get('reason', ''),
            'report': data.get('report', ''),
        }
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to parse reflect file {filepath}: {e}")
        return None


@router.get("/panel/reflect/sessions", response_model=List[ReflectSessionSummary])
def list_reflect_sessions():
    """列出所有已保存的周度反思群聊会话"""
    log_dir = _get_reflect_logs_dir()
    sessions: List[dict] = []
    if log_dir.exists():
        for f in sorted(log_dir.glob('*-reflect.json'), reverse=True):
            parsed = _parse_reflect_file(f)
            if parsed:
                sessions.append(parsed)
    return [
        ReflectSessionSummary(
            id=s['id'],
            start_date=s['start_date'],
            end_date=s['end_date'],
            stance=s['stance'],
            position_limit=s['position_limit'],
            created_at=s['created_at'],
            reason=s['reason'],
        )
        for s in sessions
    ]


@router.get("/panel/reflect/sessions/{session_id}", response_model=ReflectSessionDetail)
def get_reflect_session(session_id: str):
    """获取单次周度反思群聊的完整内容"""
    log_dir = _get_reflect_logs_dir()
    filepath = log_dir / f"{session_id}.json"
    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    parsed = _parse_reflect_file(filepath)
    if parsed is None:
        raise HTTPException(status_code=500, detail="Failed to parse session file")
    return ReflectSessionDetail(
        id=parsed['id'],
        start_date=parsed['start_date'],
        end_date=parsed['end_date'],
        stance=parsed['stance'],
        position_limit=parsed['position_limit'],
        created_at=parsed['created_at'],
        reason=parsed['reason'],
        report=parsed['report'],
    )


@router.post("/panel/reflect", response_model=PanelResponse)
def trigger_panel_reflect(req: PanelRequest):
    """阻塞版本：触发专家组群聊讨论，等待完成后返回结果。耗时约 5-9 分钟。"""
    settings = get_settings()
    pi_url = settings.PI_SERVER_URL  # e.g. http://piserver:3001/chat

    payload = json.dumps({
        "message": req.message,
        "session_id": req.session_id or "frontend_panel",
        "mode": "reflect",
        "history_messages": req.history_messages or [],
    }).encode("utf-8")

    pi_req = urllib.request.Request(
        pi_url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    ctx = ssl.create_default_context()

    try:
        with urllib.request.urlopen(pi_req, context=ctx, timeout=600) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return PanelResponse(
                reply=data.get("reply", ""),
                session_id=data.get("session_id", ""),
                elapsed_ms=data.get("elapsed_ms", 0),
            )
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=f"Pi Server 不可用: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"群聊讨论失败: {str(e)}")


@router.post("/panel/reflect/stream")
async def trigger_panel_reflect_stream(req: PanelRequest):
    """
    SSE 流式版本：使用 httpx 异步流实时转发 pi-server 的 SSE 事件。
    """
    settings = get_settings()
    pi_url = settings.PI_SERVER_URL.replace("/chat", "/chat/stream")

    payload = json.dumps({
        "message": req.message,
        "session_id": req.session_id or "frontend_panel_stream",
        "history_messages": req.history_messages or [],
    }).encode("utf-8")

    async def event_stream():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
                async with client.stream(
                    "POST", pi_url,
                    content=payload,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                ) as resp:
                    if resp.status_code != 200:
                        yield f"event: error\ndata: {{\"message\":\"Pi Server 返回 {resp.status_code}\"}}\n\n"
                        return
                    async for line in resp.aiter_lines():
                        if line:
                            yield line + "\n"
        except httpx.ConnectError as e:
            yield f"event: error\ndata: {{\"message\":\"Pi Server 不可用: {str(e)}\"}}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {{\"message\":\"{str(e)}\"}}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
