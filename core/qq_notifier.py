# -*- coding: utf-8 -*-
"""
QQ Bot notification module

Uses official QQ Bot API (V2):
- Token:  https://bots.qq.com/app/getAppAccessToken (NEW)
- Gateway: https://api.sgroup.qq.com/gateway
- WebSocket: wss://api.sgroup.qq.com/websocket
- Messages: via WebSocket (HTTP requires active WS connection)
"""
import asyncio
import json
import os
import ssl
import sys
import time
import traceback
from datetime import datetime
from typing import Optional, Callable
import urllib.request
import urllib.error

# ---- Config (from env vars, with .env support) ----
import os
from pathlib import Path

def _load_env():
    """Load .env from project root, always overriding existing vars"""
    _env_file = Path(__file__).parent.parent / ".env"
    if _env_file.exists():
        try:
            with open(_env_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key, val = key.strip(), val.strip().strip('"').strip("'")
                    if key:
                        os.environ[key] = val  # always override
            print(f"[QQ] Loaded .env from {_env_file}", file=sys.stderr)
        except Exception as e:
            print(f"[QQ] Failed to load .env: {e}", file=sys.stderr)

_load_env()

APP_ID = os.environ.get("QQ_APP_ID", "102082602")
APP_SECRET = os.environ.get("QQ_APP_SECRET", "HB50vroljhgfffghjlorvz49FLSZhqz9")
BOT_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
GATEWAY_BASE_URL = "https://api.sgroup.qq.com"
GATEWAY_WS_URL = f"{GATEWAY_BASE_URL}/gateway"

# ---- Token cache ----
_cached_token: Optional[str] = None
_token_expires_at: float = 0


def get_access_token() -> Optional[str]:
    """Get access token via bots.qq.com (new API), with retry on network errors"""
    global _cached_token, _token_expires_at

    if _cached_token and time.time() < _token_expires_at - 60:
        return _cached_token

    if not APP_SECRET:
        print(f"[QQ] ERROR: APP_SECRET is empty! APP_ID={APP_ID}", file=sys.stderr)
        return None

    max_retries = 3
    for attempt in range(max_retries):
        try:
            payload = json.dumps({
                "appId": APP_ID,
                "clientSecret": APP_SECRET
            }).encode("utf-8")

            req = urllib.request.Request(
                BOT_TOKEN_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            _cached_token = data.get("access_token")
            expires_in = int(data.get("expires_in", 7200))
            _token_expires_at = time.time() + expires_in

            print(f"[QQ] AccessToken obtained, valid for {expires_in}s", file=sys.stderr)
            return _cached_token

        except (urllib.error.URLError, OSError) as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 2
                print(f"[QQ] Network error (attempt {attempt+1}/{max_retries}), retry in {wait}s: {e}", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"[QQ] AccessToken failed after {max_retries} retries: {e}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"[QQ] AccessToken failed: {e}", file=sys.stderr)
            return None

    return None


def send_c2c_message(openid: str, content: str, msg_id: str = "", event_id: str = "") -> bool:
    """
    Send C2C private message via HTTP API.
    
    Args:
        openid: User's openid
        content: Text content
        msg_id: Message ID for passive reply (from C2C_MSG_RECEIVE event)
        event_id: Event ID for passive reply
    """
    token = get_access_token()
    if not token:
        return False

    try:
        url = f"{GATEWAY_BASE_URL}/v2/users/{openid}/messages"
        body: dict = {
            "msg_type": 2,  # Markdown
            "markdown": {"content": content},
        }
        # Passive reply: include msg_id or event_id for better rate limits
        if msg_id:
            body["msg_id"] = msg_id
            body["msg_seq"] = 1
        if event_id:
            body["event_id"] = event_id

        payload = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"QQBot {token}"
            },
            method="POST"
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print(f"[QQ] Message sent -> {openid}: {content[:50]}", file=sys.stderr)
            return True

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        print(f"[QQ] Message send failed HTTP {e.code}: {body}", file=sys.stderr)
    except Exception as e:
        print(f"[QQ] Message send failed: {e}", file=sys.stderr)

    return False


class QQBotClient:
    """QQ Bot WebSocket client with heartbeat and message handling"""

    # QQ Bot V2 intents:
    # GUILDS=1, GUILD_MEMBERS=2, GUILD_MESSAGES=512, GUILD_MESSAGE_REACTIONS=1024,
    # DIRECT_MESSAGE=4096, GROUP_AND_C2C_EVENT=33554432, INTERACTION=67108864,
    # MESSAGE_AUDIT=134217728, AUDIO_ACTION=536870912, PUBLIC_GUILD_MESSAGES=1073741824
    # For C2C + Group + Guild: 4096 | 33554432 | 512 | 1 = 33559553
    def __init__(self, intents: int = 33559553, shards: tuple = (0, 1)):
        self.intents = intents
        self.shards = shards
        self.token: Optional[str] = None
        self.ws = None
        self.running = False
        self.on_message: Optional[Callable] = None
        self.heartbeat_interval: float = 41250  # ms, default
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._seq: Optional[int] = None  # latest sequence number

    async def connect(self):
        """Connect to WebSocket gateway"""
        self.token = get_access_token()
        if not self.token:
            raise Exception("Cannot get AccessToken")

        # Get WebSocket endpoint
        req = urllib.request.Request(
            GATEWAY_WS_URL,
            headers={"Authorization": f"QQBot {self.token}"}
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            ws_url = data.get("url")

        if not ws_url:
            raise Exception("Cannot get WebSocket URL")

        print(f"[QQ] Gateway WS URL: {ws_url}", file=sys.stderr)
        await self._run(ws_url)

    async def _run(self, ws_url: str):
        """WebSocket event loop with auto-reconnect"""
        import websockets

        self.running = True

        while self.running:
            try:
                async with websockets.connect(ws_url, ssl=ssl.create_default_context()) as ws:
                    self.ws = ws
                    print(f"[QQ] WebSocket connected", file=sys.stderr)

                    # Send Identify
                    await ws.send(json.dumps({
                        "op": 2,
                        "d": {
                            "token": f"QQBot {self.token}",
                            "intents": self.intents,
                            "shard": list(self.shards),
                            "properties": {
                                "$os": "windows",
                                "$browser": "marcus",
                                "$device": "marcus"
                            }
                        }
                    }))
                    print(f"[QQ] Identify sent, intents={self.intents}", file=sys.stderr)

                    # Event loop
                    async for raw in ws:
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        await self._handle_event(event)

            except websockets.ConnectionClosed as e:
                print(f"[QQ] Connection closed: {e.code} {e.reason}", file=sys.stderr)
            except (OSError, asyncio.TimeoutError) as e:
                print(f"[QQ] Network error: {e}", file=sys.stderr)
            except Exception as e:
                print(f"[QQ] Unexpected error: {e}", file=sys.stderr)
                traceback.print_exc()

            if self.running:
                print(f"[QQ] Reconnecting in 5s...", file=sys.stderr)
                await asyncio.sleep(5)
                # Refresh token
                self.token = get_access_token()

    async def _handle_event(self, event: dict):
        """Handle WebSocket events"""
        op = event.get("op")
        d = event.get("d", {})
        t = event.get("t", "")
        s = event.get("s")  # sequence number

        if s is not None:
            self._seq = s

        if op == 10:  # Hello
            self.heartbeat_interval = d.get("heartbeat_interval", 41250)
            print(f"[QQ] Hello received, heartbeat={self.heartbeat_interval}ms", file=sys.stderr)
            self._start_heartbeat()

        elif op == 11:  # Heartbeat ACK
            pass  # silently acknowledge

        elif op == 0:  # Dispatch (event)
            # DEBUG: log all non-heartbeat events
            if t not in ("",):
                print(f"[QQ] Event: op=0 t={t} keys={list(d.keys())[:8]}", file=sys.stderr)

            if t == "READY":
                user = d.get("user", {})
                print(f"[QQ] READY! Bot online as {user.get('username', '?')}", file=sys.stderr)

            elif t in ("C2C_MESSAGE_CREATE", "C2C_MSG_RECEIVE", "DIRECT_MESSAGE_CREATE"):
                # Private chat message (V2 official event name: C2C_MSG_RECEIVE)
                author = d.get("author", {}) or d.get("sender", {})
                # 优先使用 openid（持久化标识），id 是内部临时 ID
                openid = author.get("openid", "") or author.get("id", "")
                content = d.get("content", "")
                msg_id = d.get("id", "")

                print(f"[QQ] C2C message from {openid} (msg_id={msg_id[:12]}): {content[:80]}", file=sys.stderr)

                if self.on_message:
                    asyncio.create_task(self.on_message(openid, content, msg_id))

            elif t == "GROUP_AT_MESSAGE_CREATE":
                # Group @ message
                group_openid = d.get("group_openid", "") or d.get("group_id", "")
                author = d.get("author", {}) or d.get("sender", {})
                # 优先使用 openid（持久化标识），id 是内部临时 ID
                openid = author.get("openid", "") or author.get("id", "")
                content = d.get("content", "")
                msg_id = d.get("id", "")

                print(f"[QQ] Group @ message from {openid} in {group_openid}: {content[:80]}", file=sys.stderr)

                if self.on_message:
                    asyncio.create_task(self.on_message(openid, content, msg_id, group_openid))

            elif t == "C2C_FRIEND_ADD":
                openid = d.get("openid", "")
                print(f"[QQ] Friend added: {openid}", file=sys.stderr)

            else:
                # Log ALL unknown events for debugging
                print(f"[QQ] UNHANDLED event: op=0 t={t} sample={json.dumps(d, ensure_ascii=False)[:200]}", file=sys.stderr)

    def _start_heartbeat(self):
        """Start heartbeat task"""
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self):
        """Send heartbeat at regular intervals"""
        interval = self.heartbeat_interval / 1000.0  # ms -> seconds
        while self.running and self.ws:
            try:
                await asyncio.sleep(interval)
                if self.ws:
                    await self.ws.send(json.dumps({
                        "op": 1,
                        "d": self._seq
                    }))
                    # print(f"[QQ] Heartbeat sent, seq={self._seq}", file=sys.stderr)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[QQ] Heartbeat error: {e}", file=sys.stderr)
                break

    async def send_ws_message(self, openid: str, content: str, msg_id: Optional[str] = None, event_id: Optional[str] = None):
        """
        Send C2C message via WebSocket (passive reply preferred when msg_id/event_id available).
        """
        if not self.ws:
            print("[QQ] WS not connected, cannot send message", file=sys.stderr)
            return False

        try:
            payload: dict = {
                "content": content,
                "msg_type": 0,
            }
            if msg_id:
                payload["msg_id"] = msg_id
                payload["msg_seq"] = 1
            if event_id:
                payload["event_id"] = event_id

            # QQ Bot WebSocket: send via HTTP API (not via WS opcode)
            # WebSocket is only for receiving events
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: send_c2c_message(openid, content, msg_id or "", event_id or "")
            )
            print(f"[QQ] WS reply -> {openid}: {content[:50]}", file=sys.stderr)
            return True
        except Exception as e:
            print(f"[QQ] WS message send failed: {e}", file=sys.stderr)
            return False

    def send_message(self, openid: str, content: str):
        """
        Sync send message — tries HTTP, falls back to logging.
        NOTE: for async callers, use send_ws_message() instead.
        """
        return send_c2c_message(openid, content)


async def _test_receive():
    """Test: listen for messages"""
    client = QQBotClient()

    async def on_message(openid: str, content: str, msg_id: str = "", group_openid: str = ""):
        print(f"\n>>> Message from {openid}: {content}")
        # Echo back
        await client.send_ws_message(openid, f"收到: {content}")

    client.on_message = on_message
    await client.connect()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--listen":
        print("[QQ] Starting listen mode...")
        asyncio.run(_test_receive())
    else:
        if len(sys.argv) < 3:
            print("Usage: qq_notifier.py <openid> <message>")
            print("   or: qq_notifier.py --listen")
            sys.exit(1)

        openid = sys.argv[1]
        content = " ".join(sys.argv[2:])
        success = send_c2c_message(openid, content)
        sys.exit(0 if success else 1)
