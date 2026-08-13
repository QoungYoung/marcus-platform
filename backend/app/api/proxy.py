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
        is_stream = bool(payload.get("stream"))
        if is_stream:
            # Console Go 上游对数据中心出口 IP 的 SSE 流式常返回空流（零 chunk），
            # 而非流式 JSON 正常（本地直连正常、服务器直连被掐）。强制以非流式
            # 取完整结果，再在代理层模拟 SSE 分块发给前端，对前端完全透明。
            payload["stream"] = False
        body = json.dumps(payload).encode("utf-8")

        resp = await client.post(url, headers=headers, content=body)
        try:
            data = resp.json()
        except ValueError:
            return Response(content=resp.content, status_code=resp.status_code,
                            media_type=resp.headers.get("content-type", "application/json"))
        if resp.status_code != 200:
            return JSONResponse(data, status_code=resp.status_code)

        if not is_stream:
            return JSONResponse(data, status_code=resp.status_code)

        # ── 模拟 SSE 流式响应 ──
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        reasoning = message.get("reasoning_content") or ""
        if not content and reasoning:
            # content 被思维链吃光时退而显示思维链，避免前端空白
            content = reasoning
        tool_calls = message.get("tool_calls")
        finish_reason = choice.get("finish_reason") or ("tool_calls" if tool_calls else "stop")
        created = data.get("created", 0)
        model = data.get("model", "")
        chunk_id = data.get("id", "chatcmpl-proxy")

        def sse_chunk(delta: dict, finish: Optional[str]) -> bytes:
            obj = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish, "logprobs": None}],
            }
            return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")

        async def simulated_stream():
            yield sse_chunk({"role": "assistant", "content": ""}, None)
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    yield sse_chunk({"tool_calls": [{
                        "index": tc.get("index", 0),
                        "id": tc.get("id"),
                        "type": tc.get("type") or "function",
                        "function": {"name": fn.get("name") or "", "arguments": fn.get("arguments") or ""},
                    }]}, None)
            if content:
                step = 24
                for i in range(0, len(content), step):
                    yield sse_chunk({"content": content[i:i + step]}, None)
            yield sse_chunk({}, finish_reason)
            yield b"data: [DONE]\n\n"

        return StreamingResponse(
            simulated_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
