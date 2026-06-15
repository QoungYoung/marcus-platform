"""本地采集东方财富 → 远程 PG（浏览器 Cookie）"""
import sys, os, json, time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
from curl_cffi import requests as cffi_req

# PG
db_pass = os.environ.get("DB_PASSWORD", "marcus123")
conn = psycopg2.connect(f"postgresql://marcus:{db_pass}@81.70.44.68:5432/marcus_trading")
cur = conn.cursor()
cur.execute("""
    CREATE TABLE IF NOT EXISTS fund_flow_cache (
        data_type VARCHAR(32) NOT NULL, symbol VARCHAR(32) NOT NULL DEFAULT '',
        data_json TEXT NOT NULL, updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
        PRIMARY KEY (data_type, symbol)
    )
""")
cur.execute("CREATE INDEX IF NOT EXISTS idx_ffc_type_time ON fund_flow_cache(data_type, updated_at)")
conn.commit()
print("[PG] connected")

def safe_float(v, default=0.0):
    try:
        return float(v)
    except (ValueError, TypeError):
        return default

# 浏览器 Cookie（从你的 curl 中提取）
COOKIE = "qgqp_b_id=1cc3c89ff09003f14504d6ce2704f978; st_nvi=W6lpD9Ad7PhFwtvK87DTf930b; nid18=0669c78d6e75a0345b1571c451cbd4b4; nid18_create_time=1777289270410; gviem=K3qwW0bI41sVLDrtqtPBQ2d3c; gviem_create_time=1777289270410; fullscreengg=1; fullscreengg2=1; websitepoptg_api_time=1781502832785"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://data.eastmoney.com/zjlx/detail.html",
    "Cookie": COOKIE,
    "DNT": "1",
    "sec-ch-ua": '"Microsoft Edge";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

url = "https://push2.eastmoney.com/api/qt/clist/get"
base_params = {
    "fid": "f3", "po": "0", "pz": "100", "np": "1",
    "fltt": "2", "invt": "2",
    "ut": "8dec03ba335b81bf4ebdf7b29ec27d15",
    "fs": "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2",
    "fields": "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87",
}

count = 0
for pn in range(1, 80):
    params = {**base_params, "pn": str(pn)}
    resp = cffi_req.get(url, params=params, headers=headers, impersonate="chrome124", timeout=15)
    result = resp.json()
    data = result.get("data")
    if not data:
        break
    diff = data.get("diff", [])
    if not diff:
        break
    for d in diff:
        code = str(d.get("f12", "")).zfill(6)
        data = {
            "symbol": code, "name": str(d.get("f14", "")),
            "price": safe_float(d.get("f2")),
            "change_pct": str(d.get("f3", "")),
            "main_net": safe_float(d.get("f62")),
            "main_pct": str(d.get("f184", "")),
            "lg_net": safe_float(d.get("f66")), "lg_pct": str(d.get("f69", "")),
            "md_net": safe_float(d.get("f72")), "md_pct": str(d.get("f75", "")),
            "sm_net": safe_float(d.get("f78")), "sm_pct": str(d.get("f81", "")),
            "xs_net": safe_float(d.get("f84")), "xs_pct": str(d.get("f87", "")),
        }
        cur.execute(
            "INSERT INTO fund_flow_cache (data_type, symbol, data_json, updated_at) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (data_type, symbol) DO UPDATE SET data_json=EXCLUDED.data_json, updated_at=EXCLUDED.updated_at",
            ("individual", code, json.dumps(data, ensure_ascii=False), datetime.now()),
        )
        count += 1
    print(f"  p{pn}: {count}", flush=True)
    time.sleep(0.3)

cur.execute(
    "INSERT INTO fund_flow_cache (data_type, symbol, data_json, updated_at) VALUES (%s,%s,%s,%s) "
    "ON CONFLICT (data_type, symbol) DO UPDATE SET data_json=EXCLUDED.data_json, updated_at=EXCLUDED.updated_at",
    ("individual", "__index__", json.dumps({"count": count}), datetime.now()),
)
conn.commit()
conn.close()
print(f"\nDone: {count} stocks", flush=True)
