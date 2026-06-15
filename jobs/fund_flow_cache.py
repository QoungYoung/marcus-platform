#!/usr/bin/env python3
"""资金流缓存定时任务 — 采集东方财富实时资金流，落 PostgreSQL"""
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


# ── 3. 个股资金流（同花顺 data.10jqka.com.cn，Docker 内东财不通） ──
def fetch_individual_flow():
    try:
        import akshare as ak

        def _parse(val):
            if isinstance(val, (int, float)):
                return float(val)
            s = str(val).replace(",", "").strip()
            try:
                if "亿" in s:
                    return float(s.replace("亿", "")) * 1e8
                elif "万" in s:
                    return float(s.replace("万", "")) * 1e4
                return float(s)
            except ValueError:
                return 0.0

        df = ak.stock_fund_flow_individual(symbol="即时")
        if df is None or df.empty:
            print("[fund_flow_cache] individual: 空数据", flush=True)
            return

        count = 0
        for _, row in df.iterrows():
            code = str(int(row.get("股票代码", 0))).zfill(6)
            upsert_cache("individual", code, {
                "symbol": code,
                "name": str(row.get("股票简称", "")),
                "price": float(row.get("最新价", 0) or 0),
                "change_pct": str(row.get("涨跌幅", "")),
                "turnover_rate": str(row.get("换手率", "")),
                "inflow": _parse(row.get("流入资金", 0)),
                "outflow": _parse(row.get("流出资金", 0)),
                "net_amount": _parse(row.get("净额", 0)),
                "volume": _parse(row.get("成交额", 0)),
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
