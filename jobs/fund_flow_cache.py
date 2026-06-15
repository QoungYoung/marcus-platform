#!/usr/bin/env python3
"""资金流缓存 — 采集东方财富个股资金流，落 PostgreSQL（curl_cffi + 浏览器 Cookie）"""
import sys
import os
import json
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import psycopg2
from curl_cffi import requests as cffi_req

DB_URL = os.environ["DATABASE_URL"]

# 东方财富浏览器 Cookie（首次访问分配，长期有效）
EASTMONEY_COOKIE = os.environ.get(
    "EASTMONEY_COOKIE",
    "qgqp_b_id=1cc3c89ff09003f14504d6ce2704f978; "
    "st_nvi=W6lpD9Ad7PhFwtvK87DTf930b; "
    "nid18=0669c78d6e75a0345b1571c451cbd4b4; "
    "nid18_create_time=1777289270410; "
    "gviem=K3qwW0bI41sVLDrtqtPBQ2d3c; "
    "gviem_create_time=1777289270410"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://data.eastmoney.com/zjlx/detail.html",
    "Cookie": EASTMONEY_COOKIE,
}


def get_conn():
    return psycopg2.connect(DB_URL)


def init_db():
    conn = get_conn()
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
    conn.close()


def upsert(data_type: str, symbol: str, data: dict):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO fund_flow_cache (data_type, symbol, data_json, updated_at) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (data_type, symbol) DO UPDATE SET data_json=EXCLUDED.data_json, updated_at=EXCLUDED.updated_at",
        (data_type, symbol, json.dumps(data, ensure_ascii=False), datetime.now()),
    )
    conn.commit()
    conn.close()


def _sf(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def fetch_individual():
    """采集全量个股资金流（主力/超大单/大单/中单/小单 + 占比）"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    base_params = {
        "fid": "f3", "po": "0", "pz": "100", "np": "1",
        "fltt": "2", "invt": "2",
        "ut": "8dec03ba335b81bf4ebdf7b29ec27d15",
        "fs": "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2",
        "fields": "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87",
    }

    count = 0
    try:
        for pn in range(1, 80):
            params = {**base_params, "pn": str(pn)}
            resp = cffi_req.get(url, params=params, headers=HEADERS, impersonate="chrome124", timeout=15)
            result = resp.json()
            data = result.get("data")
            if not data:
                break
            diff = data.get("diff", [])
            if not diff:
                break
            for d in diff:
                code = str(d.get("f12", "")).zfill(6)
                upsert("individual", code, {
                "symbol": code, "name": str(d.get("f14", "")),
                "price": _sf(d.get("f2")),
                "change_pct": str(d.get("f3", "")),
                "main_net": _sf(d.get("f62")),
                "main_pct": str(d.get("f184", "")),
                "lg_net": _sf(d.get("f66")), "lg_pct": str(d.get("f69", "")),
                "md_net": _sf(d.get("f72")), "md_pct": str(d.get("f75", "")),
                "sm_net": _sf(d.get("f78")), "sm_pct": str(d.get("f81", "")),
                "xs_net": _sf(d.get("f84")), "xs_pct": str(d.get("f87", "")),
            })
            count += 1
            print(f"  p{pn}: {count}", flush=True)
            time.sleep(0.3)
        upsert("individual", "__index__", {"count": count})
        print(f"[fund_flow_cache] individual: {count} 只", flush=True)
    except Exception as e:
        print(f"[fund_flow_cache] 东财不可达（{e}），依赖本地同步的 PG 缓存", flush=True)


if __name__ == "__main__":
    init_db()
    start = time.time()
    print(f"[fund_flow_cache] === {datetime.now().strftime('%H:%M:%S')} 开始 ===", flush=True)
    fetch_individual()
    print(f"[fund_flow_cache] === 完成 ({time.time() - start:.1f}s) ===", flush=True)
