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
import traceback
from datetime import datetime
from typing import Optional, Dict

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
        self.command_prefix = "!"

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
            if content.strip() == f"{self.command_prefix}reset":
                await self._reset_session(openid)
                return

            if content.strip() == f"{self.command_prefix}status":
                await self._send_status(openid)
                return

            # 转发给 Pi Server
            reply = await self._call_pi_server(content, session_id)

            # 发送回复（优先 WebSocket）
            await self._send_reply(openid, reply, msg_id)

        except Exception as e:
            error_msg = f"处理消息时出错: {str(e)}"
            print(f"[QQBotService] {error_msg}", file=sys.stderr)
            traceback.print_exc()
            await self._send_text(openid, f"[ERROR] {error_msg}")

    async def _call_pi_server(self, message: str, session_id: str) -> str:
        """调用 Pi HTTP Server，返回 AI 回复"""
        import aiohttp
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.pi_server_url,
                    json={"message": message, "session_id": session_id},
                    timeout=aiohttp.ClientTimeout(total=120),  # 2分钟超时（含工具调用）
                ) as resp:
                    data = await resp.json()
                    if resp.status == 200:
                        return data.get("reply", "(无回复)")
                    else:
                        return f"Pi Server 错误: {data.get('error', '未知错误')}"
        except aiohttp.ClientConnectorError:
            return f"⚠️ Pi Server 未启动 ({self.pi_server_url})，请先启动 Pi Server"
        except asyncio.TimeoutError:
            return "⏰ Pi Server 响应超时，请稍后重试"
        except Exception as e:
            return f"调用 Pi Server 失败: {str(e)}"

    async def _send_reply(self, openid: str, reply: str, msg_id: str = ""):
        """发送回复到 QQ（自动分段，优先 WebSocket）"""
        if not reply or reply == "(无回复)":
            return

        max_len = 2000  # QQ 消息长度限制
        if len(reply) <= max_len:
            await self._send_text(openid, reply, msg_id)
        else:
            # 分段发送
            parts = []
            remaining = reply
            while len(remaining) > max_len:
                split_at = remaining.rfind('\n', 0, max_len)
                if split_at == -1 or split_at < max_len // 2:
                    split_at = max_len
                parts.append(remaining[:split_at])
                remaining = remaining[split_at:].lstrip('\n')
            if remaining:
                parts.append(remaining)

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

    async def _reset_session(self, openid: str):
        """重置用户会话"""
        session_id = self.user_sessions.get(openid, openid)
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    self.pi_server_url.replace('/chat', '/reset'),
                    json={"session_id": session_id},
                    timeout=aiohttp.ClientTimeout(total=5),
                )
            self.user_sessions[openid] = openid
            await self._send_text(openid, "会话已重置，让我们重新开始吧！")
        except Exception as e:
            await self._send_text(openid, f"重置失败: {e}")

    async def _send_status(self, openid: str):
        """发送当前状态"""
        session_count = len(self.user_sessions)
        status_lines = [
            "Marcus QQ Bot Status",
            f"* Active Sessions: {session_count}",
            f"* Pi Server: {self.pi_server_url}",
            f"* Command prefix: {self.command_prefix}",
            "",
            "Commands:",
            f"  {self.command_prefix}reset  - Reset session",
            f"  {self.command_prefix}status - Show status",
            f"  Just type to chat with AI",
        ]
        await self._send_text(openid, '\n'.join(status_lines))

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
