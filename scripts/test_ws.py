"""Quick WebSocket test - exits after Hello"""
import asyncio, json, ssl, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'core'))
from qq_notifier import get_access_token

async def test():
    token = get_access_token()
    if not token:
        print("[FAIL] No token - check APP_SECRET in .env")
        return
    print(f"[OK] Token: {token[:20]}...")

    import urllib.request
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        "https://api.sgroup.qq.com/gateway",
        headers={"Authorization": f"QQBot {token}"}
    )
    r = urllib.request.urlopen(req, context=ctx, timeout=15)
    ws_url = json.loads(r.read().decode())["url"]
    print(f"[OK] Gateway: {ws_url}")

    import websockets
    try:
        async with websockets.connect(ws_url, ssl=ctx) as ws:
            print("[OK] WS connected")
            await ws.send(json.dumps({
                "op": 2,
                "d": {
                    "token": f"QQBot {token}",
                    "intents": 513,
                    "shard": [0, 1],
                    "properties": {"$os": "windows", "$browser": "marcus", "$device": "marcus"}
                }
            }))
            print("[OK] Identify sent, waiting...")
            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            event = json.loads(raw)
            print(f"Event: op={event.get('op')} t={event.get('t','-')}")
            if event.get("op") == 10:
                hb = event["d"].get("heartbeat_interval", "?")
                print(f"[SUCCESS] Hello received! heartbeat={hb}ms")
            elif event.get("op") == 0:
                print(f"[SUCCESS] Dispatch: {event.get('t')}")
            else:
                print(f"[WARN] Unexpected op={event.get('op')}")
    except websockets.exceptions.InvalidHandshake as e:
        print(f"[FAIL] Handshake: {e}")
    except asyncio.TimeoutError:
        print("[FAIL] Timeout - no Hello received")
    except OSError as e:
        print(f"[FAIL] Network: {e}")
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")

asyncio.run(test())
