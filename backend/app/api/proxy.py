# -*- coding: utf-8 -*-
"""
OpenAI 兼容同源代理。
浏览器直连外部模型网关会被 CORS 拦截（如 opencode.ai 不返回
Access-Control-Allow-Origin），且 Cloudflare 会拒绝无浏览器 User-Agent 的请求。
前端统一请求 /api/v1/proxy/...，由后端转发到 DEEPSEEK_API_HOST 的上游地址，
服务端转发无 CORS 限制，同时补 User-Agent 绕过 Cloudflare 拦截。
"""
import json
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.config import get_settings

router = APIRouter(tags=["proxy"])

# 伪装浏览器 UA，绕过上游 Cloudflare 拦截
PROXY_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

PROXY_TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=120.0, pool=15.0)


def _upstream_url(path: str) -> str:
    """拼上游地址：https://{DEEPSEEK_API_HOST}/v1/{path}"""
    settings = get_settings()
    host = settings.DEEPSEEK_API_HOST.rstrip("/")
    return f"https://{host}/v1/{path}"


def _build_headers(request: Request, api_key: Optional[str], json_body: bool = False) -> dict:
    headers = {
        "User-Agent": PROXY_USER_AGENT,
        "Accept": "application/json, text/event-stream",
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    auth = api_key or get_settings().DEEPSEEK_API_KEY
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    return headers


@router.api_route("/proxy/{path:path}", methods=["GET", "POST", "OPTIONS"])
async def proxy_openai(path: str, request: Request):
    """透传 /proxy/models、/proxy/chat/completions 等到上游 OpenAI 兼容接口。"""
    if request.method == "OPTIONS":
        return Response(status_code=204)

    # 优先透传前端 Authorization，其次用后端配置的 key
    auth_header = request.headers.get("authorization", "")
    api_key = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else None

    url = _upstream_url(path)
    headers = _build_headers(request, api_key, json_body=(request.method == "POST"))

    async with httpx.AsyncClient(timeout=PROXY_TIMEOUT) as client:
        if request.method == "GET":
            resp = await client.get(url, headers=headers)
            try:
                return JSONResponse(resp.json(), status_code=resp.status_code)
            except ValueError:
                return Response(content=resp.content, status_code=resp.status_code,
                                media_type=resp.headers.get("content-type", "application/json"))

        # POST
        body = await request.body()
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            payload = {}
        # Console Go 等上游只认 system/user/assistant/tool，不支持 OpenAI 的
        # developer role，统一降级为 system，否则上游 400 反序列化失败。
        if isinstance(payload.get("messages"), list):
            for msg in payload["messages"]:
                if isinstance(msg, dict) and msg.get("role") == "developer":
                    msg["role"] = "system"
            body = json.dumps(payload).encode("utf-8")
        is_stream = bool(payload.get("stream"))

        if not is_stream:
            resp = await client.post(url, headers=headers, content=body)
            try:
                return JSONResponse(resp.json(), status_code=resp.status_code)
            except ValueError:
                return Response(content=resp.content, status_code=resp.status_code,
                                media_type=resp.headers.get("content-type", "application/json"))

        # 流式：原样透传上游 SSE 字节流
        upstream_req = client.build_request("POST", url, headers=headers, content=body)
        upstream = await client.send(upstream_req, stream=True)

        if upstream.status_code != 200:
            err_body = await upstream.aread()
            await upstream.aclose()
            try:
                err_json = json.loads(err_body)
            except ValueError:
                err_json = {"detail": err_body.decode("utf-8", "replace")}
            return JSONResponse(err_json, status_code=upstream.status_code)

        async def event_stream():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            except httpx.ReadError:
                # 上游提前关闭连接视为流正常结束（SSE 无明确 EOF 标记）
                pass
            finally:
                await upstream.aclose()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
