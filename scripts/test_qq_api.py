# -*- coding: utf-8 -*-
"""QQ Bot API endpoint discovery - find the correct API URLs"""
import urllib.request, ssl, json, sys

# Get a fresh token
ctx = ssl.create_default_context()

# 1. Get token from new API
print("[Step 1] Getting token from bots.qq.com...")
payload = json.dumps({
    "appId": "102917710",
    "clientSecret": "6KZo4KbsASl4Oi3Pl8VtHg5VvMnFhAd7"
}).encode()
req = urllib.request.Request(
    "https://bots.qq.com/app/getAppAccessToken",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST"
)
r = urllib.request.urlopen(req, context=ctx, timeout=15)
data = json.loads(r.read().decode())
token = data["access_token"]
expires = data.get("expires_in", "?")
print(f"  Token: {token[:30]}...  expires_in: {expires}")

# 2. Get gateway URL
print("\n[Step 2] Getting gateway URL...")
req = urllib.request.Request(
    "https://api.sgroup.qq.com/gateway",
    headers={"Authorization": f"QQBot {token}"}
)
r = urllib.request.urlopen(req, context=ctx, timeout=15)
data = json.loads(r.read().decode())
print(f"  Gateway: {data['url']}")

# 3. Test message send endpoints
OPENID = "BF1510663A6C14D6E00E42B46108F51E"
msg_payload = json.dumps({"content": "test"}).encode()
headers = {
    "Content-Type": "application/json",
    "Authorization": f"QQBot {token}"
}

endpoints = [
    # V2 C2C (old)
    f"https://api.sgroup.qq.com/openapi/v2/c2c/{OPENID}/message",
    # V2 users
    f"https://api.sgroup.qq.com/v2/users/{OPENID}/messages",
    # V10 users
    f"https://api.sgroup.qq.com/v10/users/{OPENID}/messages",
    # Direct message
    f"https://api.sgroup.qq.com/v2/users/{OPENID}/direct-messages",
    # Bot API
    f"https://api.sgroup.qq.com/v1/users/{OPENID}/messages",
    # sandbox
    f"https://sandbox.api.sgroup.qq.com/v2/users/{OPENID}/messages",
]

print("\n[Step 3] Testing message send endpoints...")
for url in endpoints:
    try:
        req = urllib.request.Request(url, data=msg_payload, headers=headers, method="POST")
        r = urllib.request.urlopen(req, context=ctx, timeout=10)
        body = r.read().decode()
        print(f"  [OK] {url} -> {r.status} {body[:100]}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:100]
        print(f"  [{e.code}] {url} -> {body}")
    except Exception as e:
        print(f"  [ERR] {url} -> {e}")
