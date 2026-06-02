"""Test QQ Bot V2 message format"""
import urllib.request, ssl, json

# Get fresh token
ctx = ssl.create_default_context()
payload = json.dumps({"appId": "102917710", "clientSecret": "6KZo4KbsASl4Oi3Pl8VtHg5VvMnFhAd7"}).encode()
req = urllib.request.Request("https://bots.qq.com/app/getAppAccessToken", data=payload, headers={"Content-Type":"application/json"}, method="POST")
r = urllib.request.urlopen(req, context=ctx, timeout=15)
token = json.loads(r.read().decode())["access_token"]
print(f"Token: {token[:20]}...")

OPENID = "BF1510663A6C14D6E00E42B46108F51E"
headers = {"Content-Type": "application/json", "Authorization": f"QQBot {token}"}

# Test different message formats
formats = [
    ("content only", {"content": "test"}),
    ("content + msg_type=0", {"content": "test", "msg_type": 0}),
    ("content + msg_type=1", {"content": "test", "msg_type": 1}),
    ("markdown format", {"msg_type": 2, "markdown": {"content": "test"}}),
    ("msg_type=0 + content + msg_id", {"msg_type": 0, "content": "test", "msg_id": "1"}),
    ("msg_type=0 + content + msg_seq", {"msg_type": 0, "content": "test", "msg_seq": 1}),
    ("QBot msg struct", {"msg_type": 0, "content": "test", "msg_id": "1", "timestamp": "11111"}),
]

url = f"https://api.sgroup.qq.com/v2/users/{OPENID}/messages"
for desc, body in formats:
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        r = urllib.request.urlopen(req, context=ctx, timeout=10)
        print(f"  [OK] {desc}: {r.status} {r.read().decode()[:150]}")
    except urllib.error.HTTPError as e:
        msg = e.read().decode()[:150]
        print(f"  [{e.code}] {desc}: {msg}")
    except Exception as e:
        print(f"  [ERR] {desc}: {e}")
