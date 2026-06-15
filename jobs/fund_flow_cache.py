#!/usr/bin/env python3
"""资金流缓存定时任务 — 采集东方财富实时资金流，落 PostgreSQL"""
import sys
import json
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import psycopg2

# PostgreSQL 连接 — Docker 内 postgres 是服务名
DB_URL = os.environ["DATABASE_URL"]


def get_conn():
    return psycopg2.connect(DB_URL)


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS fund_flow_cache (
            data_type VARCHAR(32) NOT NULL,
            symbol VARCHAR(32) NOT NULL DEFAULT '',
            data_json TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            PRIMARY KEY (data_type, symbol)
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_ffc_type_time ON fund_flow_cache(data_type, updated_at)
    """)
    conn.commit()
    conn.close()


def upsert_cache(data_type: str, symbol: str, data: dict):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO fund_flow_cache (data_type, symbol, data_json, updated_at) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (data_type, symbol) DO UPDATE SET data_json=EXCLUDED.data_json, updated_at=EXCLUDED.updated_at",
        (data_type, symbol, json.dumps(data, ensure_ascii=False), datetime.now()),
    )
    conn.commit()
    conn.close()


# ── 1. 大盘资金流 ──
def fetch_market_flow():
    try:
        from utils.em_sector_flow import get_market_moneyflow_realtime
        rt = get_market_moneyflow_realtime()
        if rt:
            combined = rt["combined"]
            data = {
                "main_net": combined["main_net"],
                "main_net_fmt": combined["main_net_fmt"],
                "main_net_rate": combined.get("main_net_rate", 0),
                "super_large_net": combined["super_large_net"],
                "large_net": combined["large_net"],
                "medium_net": combined["medium_net"],
                "small_net": combined["small_net"],
                "total_amount": combined["total_amount"],
                "total_amount_fmt": combined["total_amount_fmt"],
                "flow_nature": rt["flow_nature"],
                "source": rt["source"],
            }
            upsert_cache("market", "", data)
            print(f"[fund_flow_cache] market: {data['main_net_fmt']}", flush=True)
    except Exception as e:
        print(f"[fund_flow_cache] market FAIL: {e}", flush=True)


# ── 2. 概念资金流 ──
def fetch_concept_flow():
    try:
        from utils.em_sector_flow import get_sector_flow
        items = get_sector_flow(sector_type="concept", sort_by="main_net", top_n=50, use_cache=False)
        if items:
            for item in items:
                name = item.get("name", "")
                upsert_cache("concept", name, {
                    "name": name,
                    "code": item.get("code", ""),
                    "pct_change": item.get("pct_change", 0),
                    "main_net": item.get("main_net", 0),
                    "main_net_rate": item.get("main_net_rate", 0),
                    "super_large_net": item.get("super_large_net", 0),
                    "large_net": item.get("large_net", 0),
                    "medium_net": item.get("medium_net", 0),
                    "small_net": item.get("small_net", 0),
                    "advancing": item.get("advancing", 0),
                    "declining": item.get("declining", 0),
                    "total_stocks": item.get("total_stocks", 0),
                    "lead_stock_name": item.get("lead_stock_name", ""),
                    "lead_stock_code": item.get("lead_stock_code", ""),
                })
            upsert_cache("concept", "__index__", {"count": len(items)})
            print(f"[fund_flow_cache] concept: {len(items)} 个", flush=True)
    except Exception as e:
        print(f"[fund_flow_cache] concept FAIL: {e}", flush=True)


# ── 3. 个股资金流 ──
def fetch_individual_flow():
    try:
        import urllib.request, urllib.parse, ssl

        url = "https://push2.eastmoney.com/api/qt/clist/get"
        ctx = ssl.create_default_context()
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "http://data.eastmoney.com/zjlx/detail.html",
        }
        base_params = {
            "fid": "f3", "po": "0", "pz": "500", "np": "1",
            "fltt": "2", "invt": "2",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
            "fs": "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2",
            "fields": "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87",
        }

        count = 0
        for pn in range(1, 15):
            params = {**base_params, "pn": str(pn)}
            full_url = url + "?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(full_url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
            result = json.loads(raw)
            diff = result.get("data", {}).get("diff", [])
            if not diff:
                break
            for d in diff:
                code = str(d.get("f12", "")).zfill(6)
                upsert_cache("individual", code, {
                    "symbol": code,
                    "name": str(d.get("f14", "")),
                    "price": float(d.get("f2", 0) or 0),
                    "change_pct": str(d.get("f3", "")),
                    "main_net": float(d.get("f62", 0) or 0),
                    "main_pct": str(d.get("f184", "")),
                    "lg_net": float(d.get("f66", 0) or 0),
                    "lg_pct": str(d.get("f69", "")),
                    "md_net": float(d.get("f72", 0) or 0),
                    "md_pct": str(d.get("f75", "")),
                    "sm_net": float(d.get("f78", 0) or 0),
                    "sm_pct": str(d.get("f81", "")),
                    "xs_net": float(d.get("f84", 0) or 0),
                    "xs_pct": str(d.get("f87", "")),
                })
                count += 1
        upsert_cache("individual", "__index__", {"count": count})
        print(f"[fund_flow_cache] individual: {count} 只", flush=True)
    except Exception as e:
        print(f"[fund_flow_cache] individual FAIL: {e}", flush=True)


if __name__ == "__main__":
    init_db()
    start = time.time()
    print(f"[fund_flow_cache] === {datetime.now().strftime('%H:%M:%S')} 开始 ===", flush=True)
    # 随机延迟 3-8s，避免与 market_scan 并发抢东财接口
    time.sleep(3 + hash(datetime.now().strftime('%S')) % 5)
    fetch_individual_flow()
    # 大盘和概念由 market_scan 采集，此处不再重复请求以避免并发冲突
    print(f"[fund_flow_cache] === 完成 ({time.time() - start:.1f}s) ===", flush=True)
