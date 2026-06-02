# -*- coding: utf-8 -*-
"""
QQ Bot connectivity diagnostic script
Tests: Token -> Gateway URL -> WebSocket handshake
"""
import asyncio
import json
import ssl
import sys
import io
import urllib.request
import urllib.error
from pathlib import Path

# Fix Windows GBK encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent.parent / "core"))
from qq_notifier import APP_ID, APP_SECRET, GATEWAY_URL

OK = "[OK]"
FAIL = "[FAIL]"
HINT = "[HINT]"
WARN = "[WARN]"
INFO = "[INFO]"

print("=" * 50)
print("  QQ Bot Connectivity Diagnostic")
print("=" * 50)
print(f"  APP_ID: {APP_ID[:4]}****")
print(f"  APP_SECRET: {'configured' if APP_SECRET else 'MISSING!'}")
print(f"  Gateway: {GATEWAY_URL}")
print()

# Step 1: Get AccessToken
print("[1/3] Getting AccessToken...")
try:
    ctx = ssl.create_default_context()
    payload = json.dumps({
        "grant_type": "client_credentials",
        "client_id": APP_ID,
        "client_secret": APP_SECRET
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{GATEWAY_URL}/oauth2/access_token",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    token = data.get("access_token")
    if token:
        print(f"  {OK} Token obtained (first 20 chars: {token[:20]}...)")
    else:
        print(f"  {FAIL} Token response: {json.dumps(data, ensure_ascii=False)}")
        sys.exit(1)
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8") if e.fp else ""
    print(f"  {FAIL} HTTP {e.code}: {body}")
    sys.exit(1)
except urllib.error.URLError as e:
    print(f"  {FAIL} Network error: {e.reason}")
    print(f"  {HINT} Cannot reach api.sgroup.qq.com, check network/proxy")
    sys.exit(1)
except Exception as e:
    print(f"  {FAIL} Unknown error: {e}")
    sys.exit(1)

# Step 2: Get WebSocket gateway URL
print("\n[2/3] Getting WebSocket gateway URL...")
try:
    req = urllib.request.Request(
        f"{GATEWAY_URL}/gateway",
        headers={"Authorization": f"QQBot {token}"}
    )
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    ws_url = data.get("url", "")
    if ws_url:
        print(f"  {OK} Gateway URL: {ws_url[:80]}...")
        if ws_url.startswith("wss://"):
            print(f"  {OK} Protocol: WebSocket Secure (wss)")
        else:
            protocol = ws_url.split('://')[0] if '://' in ws_url else 'unknown'
            print(f"  {WARN} Protocol: {protocol}")
    else:
        print(f"  {FAIL} No gateway URL: {json.dumps(data, ensure_ascii=False)}")
        sys.exit(1)
except Exception as e:
    print(f"  {FAIL} Get gateway failed: {e}")
    sys.exit(1)

# Step 3: WebSocket handshake
print("\n[3/3] WebSocket handshake test...")
try:
    import websockets

    async def test_ws():
        try:
            async with websockets.connect(
                ws_url,
                ssl=ssl.create_default_context(),
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                print(f"  {OK} WebSocket connected")

                # Send Identify
                identify = json.dumps({
                    "op": 2,
                    "d": {
                        "token": f"QQBot {token}",
                        "intents": 513,
                        "shard": [0, 1],
                        "properties": {
                            "$os": "windows",
                            "$browser": "marcus",
                            "$device": "marcus"
                        }
                    }
                })
                await ws.send(identify)
                print(f"  {OK} Identify sent")

                # Wait for Hello (op=10) or Ready (op=0, t=READY)
                for i in range(3):
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=10)
                        event = json.loads(raw)
                        op = event.get("op")
                        t = event.get("t", "")

                        if op == 10:
                            heartbeat = event["d"].get("heartbeat_interval", "?")
                            print(f"  {OK} Received Hello (op=10), heartbeat: {heartbeat}ms")
                            print(f"  {OK} QQ Bot WebSocket connection is FULLY WORKING!")
                            return True
                        elif op == 0 and t == "READY":
                            print(f"  {OK} Received READY event, bot is online!")
                            print(f"  {OK} QQ Bot WebSocket connection is FULLY WORKING!")
                            return True
                        elif op == 0:
                            print(f"  {INFO} Received event: {t}")
                        else:
                            print(f"  {INFO} Received op={op}")
                    except asyncio.TimeoutError:
                        print(f"  {WARN} Timeout waiting for Hello (attempt {i+1}/3)")
                        if i == 2:
                            print(f"  {HINT} Connection OK but no Hello. Check intents/bot status")
                        continue

                print(f"  {WARN} Did not receive expected Hello/Ready event")
                return False
        except websockets.exceptions.InvalidURI as e:
            print(f"  {FAIL} Invalid WebSocket URI: {e}")
        except websockets.exceptions.InvalidHandshake as e:
            print(f"  {FAIL} WebSocket handshake failed: {e}")
            print(f"  {HINT} Token may be invalid or expired")
        except ssl.SSLError as e:
            print(f"  {FAIL} SSL error: {e}")
            print(f"  {HINT} Certificate verification failed")
        except asyncio.TimeoutError:
            print(f"  {FAIL} WebSocket connection timeout")
            print(f"  {HINT} Gateway unreachable, check firewall/proxy")
        except OSError as e:
            print(f"  {FAIL} Network error: {e}")
            print(f"  {HINT} Cannot connect to WebSocket server")
        except Exception as e:
            print(f"  {FAIL} WebSocket connection failed: {type(e).__name__}: {e}")
        return False

    result = asyncio.run(test_ws())
    if result:
        print("\n" + "=" * 50)
        print("  PASSED! QQ Bot can connect normally")
        print("=" * 50)
    else:
        print("\n" + "=" * 50)
        print("  FAILED! Check the errors above")
        print("=" * 50)

except ImportError:
    print(f"  {FAIL} Missing 'websockets' library. Install: pip install websockets")
except Exception as e:
    print(f"  {FAIL} Diagnostic exception: {e}")
