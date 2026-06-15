"""本地采集东方财富个股+大盘+概念资金流 → 远程 PG"""
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
    try: return float(v)
    except (ValueError, TypeError): return default

def upsert(dtype, symbol, data):
    cur.execute(
        "INSERT INTO fund_flow_cache (data_type, symbol, data_json, updated_at) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (data_type, symbol) DO UPDATE SET data_json=EXCLUDED.data_json, updated_at=EXCLUDED.updated_at",
        (dtype, symbol, json.dumps(data, ensure_ascii=False), datetime.now()),
    )

COOKIE = "qgqp_b_id=1cc3c89ff09003f14504d6ce2704f978; st_nvi=W6lpD9Ad7PhFwtvK87DTf930b; nid18=0669c78d6e75a0345b1571c451cbd4b4; nid18_create_time=1777289270410; gviem=K3qwW0bI41sVLDrtqtPBQ2d3c; gviem_create_time=1777289270410"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0",
    "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://data.eastmoney.com/zjlx/detail.html",
    "Cookie": COOKIE, "DNT": "1",
}

# ═══════ 1. 个股资金流 ═══════
print("\n[1/3] 个股资金流...")
url = "https://push2.eastmoney.com/api/qt/clist/get"
base = {
    "fid": "f3", "po": "0", "pz": "100", "np": "1",
    "fltt": "2", "invt": "2",
    "ut": "8dec03ba335b81bf4ebdf7b29ec27d15",
    "fs": "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2",
    "fields": "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87",
}
count = 0
for pn in range(1, 80):
    params = {**base, "pn": str(pn)}
    resp = cffi_req.get(url, params=params, headers=HEADERS, impersonate="chrome124", timeout=15)
    result = resp.json()
    d = result.get("data")
    if not d: break
    diff = d.get("diff", [])
    if not diff: break
    for row in diff:
        code = str(row.get("f12", "")).zfill(6)
        upsert("individual", code, {
            "symbol": code, "name": str(row.get("f14", "")),
            "price": safe_float(row.get("f2")),
            "change_pct": str(row.get("f3", "")),
            "main_net": safe_float(row.get("f62")), "main_pct": str(row.get("f184", "")),
            "lg_net": safe_float(row.get("f66")), "lg_pct": str(row.get("f69", "")),
            "md_net": safe_float(row.get("f72")), "md_pct": str(row.get("f75", "")),
            "sm_net": safe_float(row.get("f78")), "sm_pct": str(row.get("f81", "")),
            "xs_net": safe_float(row.get("f84")), "xs_pct": str(row.get("f87", "")),
        })
        count += 1
    print(f"  p{pn}: {count}", flush=True)
    time.sleep(0.3)
upsert("individual", "__index__", {"count": count})
print(f"  Done: {count} stocks")

# ═══════ 2. 大盘资金流 ═══════
print("\n[2/3] 大盘资金流...")
HEADERS["Referer"] = "https://data.eastmoney.com/zjlx/dpzjlx.html"
mkt_url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
mkt_fields = "f6,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f64,f65,f70,f71,f76,f77,f82,f83"
params = {"fltt": "2", "secids": "1.000001,0.399001", "fields": mkt_fields,
          "ut": "8dec03ba335b81bf4ebdf7b29ec27d15"}
resp = cffi_req.get(mkt_url, params=params, headers=HEADERS, impersonate="chrome124", timeout=15)
result = resp.json()
items = result.get("data", {}).get("diff", [])
if len(items) >= 2:
    sh, sz = items[0], items[1]
    combined = {}
    for key in ['f62','f66','f72','f78','f84','f64','f70','f76','f82','f65','f71','f77','f83']:
        combined[key] = safe_float(sh.get(key)) + safe_float(sz.get(key))
    main_net = combined.get("f62", 0)
    total = safe_float(sh.get("f6")) + safe_float(sz.get("f6"))
    data = {
        "main_net": main_net,
        "main_net_fmt": f"{main_net/10000:+.2f}亿" if abs(main_net)>=1e8 else f"{main_net/10000:+.0f}万",
        "main_net_rate": round(main_net / total * 100, 2) if total > 0 else 0,
        "super_large_net": combined.get("f66", 0),
        "large_net": combined.get("f72", 0),
        "medium_net": combined.get("f78", 0),
        "small_net": combined.get("f84", 0),
        "total_amount": total,
        "total_amount_fmt": f"{total/1e8:.2f}亿" if total>=1e8 else f"{total/1e4:.0f}万",
        "flow_nature": "主力建仓" if main_net>0 and (main_net/total>0.1 if total>0 else False)
                       else "温和流入" if main_net>0
                       else "主力出货" if main_net<0 and (abs(main_net)/total>0.08 if total>0 else False)
                       else "温和流出" if main_net<0
                       else "平衡",
        "source": "em_push2_ulist_realtime",
    }
    upsert("market", "", data)
    print(f"  Done: {data['main_net_fmt']} | {data['flow_nature']}")
else:
    print("  FAIL: <2 items")

# ═══════ 3. 概念资金流 ═══════
print("\n[3/3] 概念资金流...")
HEADERS["Referer"] = "https://data.eastmoney.com/zjlx/detail.html"
concept_base = {
    "fid": "f62", "po": "1", "pz": "50", "pn": "1", "np": "1",
    "fltt": "2", "invt": "2",
    "ut": "8dec03ba335b81bf4ebdf7b29ec27d15",
    "fs": "m:90+t:2",
    "fields": "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f104,f105,f128,f140",
}
resp = cffi_req.get(url, params=concept_base, headers=HEADERS, impersonate="chrome124", timeout=15)
result = resp.json()
diff = result.get("data", {}).get("diff", [])
c_count = 0
for row in diff[:50]:
    name = str(row.get("f14", ""))
    super_large = safe_float(row.get("f66"))
    large = safe_float(row.get("f72"))
    main_net = safe_float(row.get("f62"))
    upsert("concept", name, {
        "name": name, "code": str(row.get("f12", "")),
        "pct_change": safe_float(row.get("f3")),
        "main_net": main_net, "main_net_rate": safe_float(row.get("f184")),
        "super_large_net": super_large, "large_net": large,
        "medium_net": safe_float(row.get("f78")), "small_net": safe_float(row.get("f84")),
        "advancing": safe_float(row.get("f104")), "declining": safe_float(row.get("f105")),
        "total_stocks": safe_float(row.get("f104")) + safe_float(row.get("f105")),
        "lead_stock_name": str(row.get("f128", "")),
        "lead_stock_code": str(row.get("f140", "")),
    })
    c_count += 1
upsert("concept", "__index__", {"count": c_count})
print(f"  Done: {c_count} concepts")

conn.commit()
conn.close()
print(f"\nAll done: {count} stocks + market + {c_count} concepts", flush=True)
