# -*- coding: utf-8 -*-
"""
专家组群聊讨论 API — 前端触发 reflect panel discussion 的中转端点。
"""
import json
import urllib.request
import ssl
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from app.config import get_settings

router = APIRouter(tags=["panel"])


class PanelRequest(BaseModel):
    message: str = Field(..., min_length=1, description="反思任务描述")


class PanelResponse(BaseModel):
    reply: str
    session_id: str
    mode: str = "reflect"
    elapsed_ms: int


@router.post("/panel/reflect", response_model=PanelResponse)
def trigger_panel_reflect(req: PanelRequest):
    """阻塞版本：触发专家组群聊讨论，等待完成后返回结果。耗时约 5-9 分钟。"""
    settings = get_settings()
    pi_url = settings.PI_SERVER_URL  # e.g. http://piserver:3001/chat

    payload = json.dumps({
        "message": req.message,
        "session_id": "frontend_panel",
        "mode": "reflect",
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
        "session_id": "frontend_panel_stream",
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
