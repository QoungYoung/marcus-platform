# -*- coding: utf-8 -*-
"""
QQ Bot 服务 — 桥接 QQ 消息 ↔ Pi Agent（工具调用）

职责：
1. 启动 QQBotClient WebSocket 监听器，接收 QQ 消息
2. 将用户消息转发给 Pi HTTP Server (Node.js)，由 Pi Agent 处理工具调用
3. 将 Pi 的回复发送回 QQ
4. 提供 send_notification() 供调度器使用
"""
import asyncio
import json
import sys
import time
import traceback
from datetime import datetime
from typing import Callable, Optional, Dict

import aiohttp

# 确保 core 目录在 path 中
from pathlib import Path
_core_dir = Path(__file__).parent.parent.parent.parent / "core"
if str(_core_dir) not in sys.path:
    sys.path.insert(0, str(_core_dir))

from qq_notifier import QQBotClient, send_c2c_message, get_access_token
from app.config import get_settings

# ===== 配置 =====
PI_SERVER_URL = get_settings().PI_SERVER_URL
QQ_RECIPIENT = None  # 将在启动时从 tasks.yaml 读取
# /chat 为同步请求/响应：bridge 等整个 Agent turn（工具调用+全文生成）结束后才返回。
# 长文本回复实测可到 ~118s（6397 字），120s 硬超时会被"误杀"；提到 180s 留足余量
# （只放宽等待，不改模型输出）。根治方案见二期：/chat/stream 增量推送（QQ 分段发送）。
PI_SERVER_TIMEOUT = 180  # 秒

# QQ 投递侧约束（不影响模型输出，仅控制发到 QQ 的内容）：
MAX_QQ_MSG_LEN = 2000     # QQ 单条消息长度上限
MAX_QQ_REPLY_CHARS = 6000  # 单次回复投递上限：超出部分按段落截断并注明

# 流式攒段发送参数（二期 /chat/stream 增量 → 按段落边界分段发 QQ）：
QQ_STREAM_FLUSH_CHARS = 500   # 攒够该字符数就发送一段（无段落边界时的兜底阈值）
QQ_STREAM_BOUNDARY_MIN = 150  # 段落边界出现且攒够该字符数时立即按段落发送
QQ_THINKING_HINT_DELAY = 5    # 流式开始后若 N 秒无任何输出，先发「正在思考」提示（模型思考期可能 30-60s）
QQ_REHINT_INTERVAL = 60    # 思考/工具调用长期无输出时，每隔 N 秒补发一次「还在处理中」
QQ_STREAM_TOTAL_TIMEOUT = 600   # 流式整体护栏（秒）：心跳持续到达时不会触发
QQ_STREAM_SOCK_READ = 240       # 块间静默上限（秒）：bridge 每 20s 心跳，240s 静默=连接/桥已死


def _now_context() -> str:
    """当前时间上下文（AI 不知道实时时间，发送 QQ 消息时带上）。"""
    now = datetime.now()
    weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    return f"现在是 {now.strftime('%Y-%m-%d %H:%M:%S')} 星期{weekdays[now.weekday()]}"


def _truncate_reply_by_paragraph(text: str, cap: int = MAX_QQ_REPLY_CHARS):
    """超长回复按段落（空行→换行→硬切）截断，返回 (截断后文本, 是否截断)。

    仅在投递侧生效：模型仍生成完整回复，只是发到 QQ 的内容被段落级裁剪。
    """
    if len(text) <= cap:
        return text, False
    cut = text.rfind('\n\n', 0, cap)
    if cut < cap // 2:
        cut = text.rfind('\n', 0, cap)
    if cut < cap // 2:
        cut = cap
    return text[:cut].rstrip('\n ') + "\n\n…（回复过长，已按段落截断，后续内容省略）", True


def _split_reply_into_parts(text: str, max_len: int = MAX_QQ_MSG_LEN):
    """按段落/换行边界把长回复切成 ≤max_len 的分段（段内硬切兜底）。"""
    if len(text) <= max_len:
        return [text]
    parts = []
    remaining = text
    while len(remaining) > max_len:
        split_at = remaining.rfind('\n\n', 0, max_len)
        if split_at < max_len // 2:
            split_at = remaining.rfind('\n', 0, max_len)
        if split_at < max_len // 2:
            split_at = max_len
        parts.append(remaining[:split_at].rstrip('\n'))
        remaining = remaining[split_at:].lstrip('\n')
    if remaining:
        parts.append(remaining)
    return parts


class QQBotService:
    """
    QQ Bot 服务单例
    
    用法：
        service = QQBotService()
        service.set_pi_server_url(get_settings().PI_SERVER_URL)
        await service.start()   # 启动 WebSocket 监听
        service.send_notification(openid, message)  # 发通知
    """

    def __init__(self):
        self.client = None
        self.pi_server_url: str = PI_SERVER_URL
        self.default_recipient: Optional[str] = None
        self.running = False
        # 用户会话映射：openid → session_id
        self.user_sessions: Dict[str, str] = {}
        # 简单的命令前缀
        self.command_prefix = "/"

    def set_pi_server_url(self, url: str):
        """设置 Pi Server URL"""
        self.pi_server_url = url

    def set_default_recipient(self, openid: str):
        """设置默认通知接收人"""
        self.default_recipient = openid

    async def start(self, default_recipient: Optional[str] = None):
        """启动 QQ Bot WebSocket 监听器"""
        if self.running:
            print("[QQBotService] 已在运行中", file=sys.stderr)
            return

        if default_recipient:
            self.default_recipient = default_recipient

        self.client = QQBotClient(intents=33559553, shards=(0, 1))  # C2C + Group + Guild
        self.client.on_message = self._on_message

        print(f"[QQBotService] 启动 QQ Bot WebSocket 监听...", file=sys.stderr)
        print(f"[QQBotService] Pi Server: {self.pi_server_url}", file=sys.stderr)
        print(f"[QQBotService] 默认通知对象: {self.default_recipient}", file=sys.stderr)

        self.running = True
        try:
            await self.client.connect()
        except Exception as e:
            self.running = False
            print(f"[QQBotService] 连接失败: {e}", file=sys.stderr)
            traceback.print_exc()

    async def stop(self):
        """停止 QQ Bot 服务"""
        self.running = False
        if self.client:
            self.client.running = False
        print("[QQBotService] 已停止", file=sys.stderr)

    async def _on_message(self, openid: str, content: str, msg_id: str = "", group_openid: str = ""):
        """处理收到的 QQ 消息 — 转发给 Pi Agent"""
        try:
            context = f"[群聊:{group_openid}]" if group_openid else ""
            print(f"[QQBotService] 收到消息 [{openid}]{context}: {content[:100]}", file=sys.stderr)

            # 获取或创建用户的会话 ID（群聊按 group_openid 隔离）
            if group_openid:
                session_id = self.user_sessions.get(group_openid, group_openid)
                self.user_sessions[group_openid] = session_id
            else:
                session_id = self.user_sessions.get(openid, openid)
                self.user_sessions[openid] = session_id
            
            print(f"[QQBotService] session_id={session_id} (user={openid}), cached={session_id in self.user_sessions}", file=sys.stderr)

            # 特殊命令处理
            if content.strip() == f"{self.command_prefix}new":
                await self._reset_session(openid, group_openid)
                return

            if content.strip() == f"{self.command_prefix}status":
                await self._send_status(openid, group_openid)
                return

            # 转发给 Pi Server（流式：边生成边攒段发 QQ，不改模型输出）
            try:
                reply, new_sid = await self._chat_streaming(openid, content, session_id, msg_id)
                if new_sid and new_sid != session_id:
                    # 会话迁移（bridge fork 后返回新 session_id）：更新映射，
                    # 后续消息用新会话（保留历史、driver 正常）
                    self.user_sessions[session_id] = new_sid
                    print(f"[QQBotService] 会话已迁移: {session_id[:16]}... -> {new_sid[:24]}...", file=sys.stderr)
            except asyncio.TimeoutError:
                await self._send_text(openid, "⏰ Pi Server 响应超时，请稍后重试", msg_id)
            except aiohttp.ClientConnectorError:
                await self._send_text(openid, f"⚠️ Pi Server 未启动 ({self.pi_server_url})，请先启动 Pi Server", msg_id)
            except Exception as e:
                error_msg = f"调用 Pi Server 失败: {str(e)}"
                print(f"[QQBotService] {error_msg}", file=sys.stderr)
                traceback.print_exc()
                await self._send_text(openid, error_msg, msg_id)

        except Exception as e:
            error_msg = f"处理消息时出错: {str(e)}"
            print(f"[QQBotService] {error_msg}", file=sys.stderr)
            traceback.print_exc()
            await self._send_text(openid, f"[ERROR] {error_msg}")

    async def _call_pi_server(self, message: str, session_id: str) -> str:
        """调用 Pi HTTP Server（同步 /chat），返回 AI 回复（保留供非流式路径回退）"""
        import aiohttp
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.pi_server_url,
                    json={"message": message, "session_id": session_id},
                    timeout=aiohttp.ClientTimeout(total=PI_SERVER_TIMEOUT),  # 同步等待整个 turn（含工具调用）
                ) as resp:
                    data = await resp.json()
                    if resp.status == 200:
                        # 会话迁移（bridge fork 后返回新 session_id）：更新映射，
                        # 后续消息用新会话（保留历史、driver 正常）
                        new_sid = data.get("session_id")
                        if new_sid and new_sid != session_id:
                            self.user_sessions[session_id] = new_sid
                            print(f"[QQBotService] 会话已迁移: {session_id[:16]}... -> {new_sid[:24]}...", file=sys.stderr)
                        return data.get("reply", "(无回复)")
                    else:
                        return f"Pi Server 错误: {data.get('error', '未知错误')}"
        except aiohttp.ClientConnectorError:
            return f"⚠️ Pi Server 未启动 ({self.pi_server_url})，请先启动 Pi Server"
        except asyncio.TimeoutError:
            return "⏰ Pi Server 响应超时，请稍后重试"
        except Exception as e:
            return f"调用 Pi Server 失败: {str(e)}"


    async def _call_pi_server_stream(self, message: str, session_id: str, on_delta: Optional[Callable[[str], object]] = None, on_heartbeat: Optional[Callable[[], object]] = None) -> Dict[str, str]:
        """SSE 增量调用 /chat/stream（chat 模式）；on_delta(delta_text) 每次增量回调。

        返回 {"reply", "session_id"}；失败抛异常（调用方降级提示）。
        流式语义：bridge 在思考/工具调用静默期每 20s 发 heartbeat 保活；
        sock_read 是块间静默上限（240s，桥每 20s 心跳 → 240s 静默=连接已死），
        total 是整体护栏（600s）。长文本/长思考不会被误杀，且不限制模型输出。
        """
        import json as _json
        stream_url = self.pi_server_url.replace('/chat', '/chat/stream')
        async with aiohttp.ClientSession() as session:
            async with session.post(
                stream_url,
                json={"message": message, "session_id": session_id, "mode": "chat"},
                timeout=aiohttp.ClientTimeout(total=QQ_STREAM_TOTAL_TIMEOUT, sock_read=QQ_STREAM_SOCK_READ),
            ) as resp:
                if resp.status != 200:
                    body_text = await resp.text()
                    raise RuntimeError(f"Pi Server 返回 {resp.status}: {body_text[:200]}")
                event = None
                reply = ""
                new_session_id = session_id
                while True:
                    raw = await resp.content.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line.startswith("event:"):
                        event = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        data = _json.loads(line[len("data:"):].strip() or "{}")
                        if event == "delta":
                            delta = data.get("text", "")
                            if delta and on_delta is not None:
                                _res = on_delta(delta)
                                if asyncio.iscoroutine(_res):
                                    await _res
                        elif event == "done":
                            reply = data.get("reply", "")
                            new_session_id = data.get("session_id", session_id)
                        elif event == "heartbeat":
                            # 桥保活心跳：思考/工具调用静默期每 20s 一条（读到此行即重置 sock_read）
                            if on_heartbeat is not None:
                                _res = on_heartbeat()
                                if asyncio.iscoroutine(_res):
                                    await _res
                        elif event == "error":
                            raise RuntimeError(data.get("message", "流式生成失败"))
                return {"reply": reply, "session_id": new_session_id}

    async def _chat_streaming(self, openid: str, message: str, session_id: str, msg_id: str = ""):
        """流式聊天：SSE 增量 → 攒段 → 按段落边界发 QQ（投递侧，不改模型）。

        返回 (reply, new_session_id)；已发送内容由本方法直接推送到 QQ。
        发送前自动附加当前时间上下文（AI 不知道实时时间）。
        """
        message = f"{_now_context()}。用户消息：{message}"
        received = ""
        sent_pos = 0
        flushed = False
        hint_sent = False
        last_hint_at = 0
        hint_task = None

        async def flush_upto(end: int):
            nonlocal sent_pos, flushed
            seg = received[sent_pos:end].rstrip('\n')
            if not seg:
                return
            flushed = True
            for part in _split_reply_into_parts(seg):
                await self._send_text(openid, part, msg_id)
                await asyncio.sleep(0.5)
            sent_pos = end  # 推进已发送位置，避免重复发送

        async def on_delta(delta: str):
            nonlocal received
            received += delta
            window = received[sent_pos:]
            boundary = window.rfind('\n\n')
            if len(window) >= QQ_STREAM_FLUSH_CHARS or boundary >= QQ_STREAM_BOUNDARY_MIN:
                # 有段落边界：发到该段落结束；无边界（整段超长）：整体发出
                end = sent_pos + (boundary + 2) if boundary >= QQ_STREAM_BOUNDARY_MIN else len(received)
                await flush_upto(end)

        # 思考期提示：模型思考阶段可能 30-60s 无可见输出，先发提示避免用户以为卡死
        async def maybe_send_thinking_hint():
            nonlocal hint_sent, last_hint_at
            await asyncio.sleep(QQ_THINKING_HINT_DELAY)
            if not flushed and not hint_sent:
                hint_sent = True
                last_hint_at = time.time()
                await self._send_text(openid, "🤔 正在思考，请稍候…", msg_id)

        # 桥心跳：思考/工具调用静默期保活；若长期仍无输出，周期性补发「还在处理中」
        async def on_heartbeat():
            nonlocal last_hint_at
            if not flushed and hint_sent and (time.time() - last_hint_at) >= QQ_REHINT_INTERVAL:
                last_hint_at = time.time()
                await self._send_text(openid, "⏳ 还在处理中，请稍候…", msg_id)

        hint_task = asyncio.create_task(maybe_send_thinking_hint())
        try:
            result = await self._call_pi_server_stream(message, session_id, on_delta, on_heartbeat=on_heartbeat)
        finally:
            if hint_task:
                hint_task.cancel()
        reply = result.get("reply", "")
        new_session_id = result.get("session_id", session_id)

        # 收尾：发送剩余内容
        await flush_upto(len(received))
        if not flushed and reply and reply != "(无回复)":
            # 一次性返回（短回复 / 流式未产出 delta）：走原 _send_reply（截断+分段）
            await self._send_reply(openid, reply, msg_id)
        elif flushed and reply and len(reply) > len(received):
            # chunk 与实际 reply 有差异（如 adapter 未发全 delta）：补发缺失尾部
            tail = reply[len(received):]
            if tail.strip():
                await self._send_reply(openid, tail, msg_id)
        return reply, new_session_id

    async def _send_reply(self, openid: str, reply: str, msg_id: str = ""):
        """发送回复到 QQ（按段落截断 + 自动分段）。

        投递侧处理，不改模型：超长回复先按段落截断到 MAX_QQ_REPLY_CHARS，
        再按段落/换行边界切成 ≤MAX_QQ_MSG_LEN 的分段逐条发送。
        """
        if not reply or reply == "(无回复)":
            return

        reply, _ = _truncate_reply_by_paragraph(reply)
        parts = _split_reply_into_parts(reply)
        if len(parts) == 1:
            await self._send_text(openid, parts[0], msg_id)
            return

        for i, part in enumerate(parts):
            prefix = f"[{i+1}/{len(parts)}]\n" if len(parts) > 1 else ""
            await self._send_text(openid, prefix + part)
            if i < len(parts) - 1:
                await asyncio.sleep(0.5)

    async def _send_text(self, openid: str, content: str, msg_id: str = ""):
        """发送文本消息（带 msg_id 作为被动回复，享受更高的频控额度）"""
        if not content or not openid:
            return
        try:
            # HTTP API 发送，带上 msg_id 作为被动回复
            send_c2c_message(openid, content, msg_id)
        except Exception as e:
            print(f"[QQBotService] Send failed: {e}", file=sys.stderr)

    async def _reset_session(self, openid: str, group_openid: str = ""):
        """重置用户会话（群聊时使用 group_openid 作为会话 key）"""
        # 群聊：会话 key 是 group_openid；私聊：会话 key 是 openid
        session_key = group_openid if group_openid else openid
        session_id = self.user_sessions.get(session_key, session_key)
        import aiohttp
        reset_url = self.pi_server_url.replace('/chat', '/reset')
        print(f"[QQBotService] 重置会话: session_key={session_key}, session_id={session_id}, reset_url={reset_url}", file=sys.stderr)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    reset_url,
                    json={"session_id": session_id},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    resp_body = await resp.text()
                    print(f"[QQBotService] Pi Server 响应: status={resp.status}, body={resp_body}", file=sys.stderr)
                    if resp.status != 200:
                        raise Exception(f"Pi Server 返回 {resp.status}: {resp_body}")
            # 清除本地用户会话缓存，重置为初始值
            if session_key in self.user_sessions:
                del self.user_sessions[session_key]
            reply_openid = group_openid if group_openid else openid
            await self._send_text(reply_openid, "会话已重置，让我们重新开始吧！")
        except Exception as e:
            print(f"[QQBotService] 重置会话失败: {e}", file=sys.stderr)
            await self._send_text(openid, f"重置失败: {e}")

    async def _send_status(self, openid: str, group_openid: str = ""):
        """发送当前状态"""
        reply_openid = group_openid if group_openid else openid
        session_count = len(self.user_sessions)
        status_lines = [
            "Marcus QQ Bot Status",
            f"* Active Sessions: {session_count}",
            f"* Pi Server: {self.pi_server_url}",
            f"* Command prefix: {self.command_prefix}",
            "",
            "Commands:",
            f"  {self.command_prefix}new    - 新对话",
            f"  {self.command_prefix}status - Show status",
            f"  Just type to chat with AI",
        ]
        await self._send_text(reply_openid, '\n'.join(status_lines))

    def _send_to_qq(self, openid: str, content: str):
        """发送 QQ 消息（同步回退，供非异步调用者使用）"""
        try:
            if not content or not openid:
                return
            send_c2c_message(openid, content)
        except Exception as e:
            print(f"[QQBotService] Send failed: {e}", file=sys.stderr)

    def send_notification(self, message: str, openid: Optional[str] = None):
        """发送通知消息（供调度器等外部同步调用）"""
        target = openid or self.default_recipient
        if not target:
            print(f"[QQBotService] No recipient for notification", file=sys.stderr)
            return
        self._send_to_qq(target, message)


# ===== 全局单例 =====
qqbot_service = QQBotService()


def get_qqbot_service() -> QQBotService:
    """获取 QQ Bot 服务单例"""
    return qqbot_service


def send_qq_notification(message: str, openid: Optional[str] = None):
    """
    便捷函数：发送 QQ 通知
    
    供调度器等模块直接调用，无需关心异步细节
    """
    service = get_qqbot_service()
    service.send_notification(message, openid)
