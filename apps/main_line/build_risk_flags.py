# -*- coding: utf-8 -*-
"""build_risk_flags.py — risk_flags 结构化采集 v1
数据源(真实): tushare gyzcloud forecast(业绩预告) / express(业绩快报) / DB stock_pool(is_st)
用法:
  python -u apps/main_line/build_risk_flags.py --stocks 300308.SZ,300502.SZ
  python -u apps/main_line/build_risk_flags.py            # watch: rotation_crowding top 或 data/risk_watch.txt
规则: earnings_bad = 最新 forecast type∈{首亏,续亏} 或 (预减/略减 且 p_change_max < -30)
      earnings_clear = 最新 forecast 已披露且非 bad
输出: postgres risk_flags(symbol, flag_type, value, ann_date, source, updated_at)
"""
import os, sys, json, time, urllib.request, gzip, argparse
try:
    import psycopg2
except Exception as e:
    print("ERR psycopg2:", e); sys.exit(1)

TOKEN = os.getenv("TUSHARE_TOKEN", "")
URL = os.getenv("TUSHARE_API_URL", "https://ts.gyzcloud.top/api")
DB = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
DATA = os.environ.get("DATA_DIR", "data")
BAD_TYPES = {"首亏", "续亏"}

def call(api, params, fields):
    body = {"api_name": api, "token": TOKEN, "params": params, "fields": fields}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    try:
        d = json.loads(raw.decode())
    except UnicodeDecodeError:
        d = json.loads(gzip.decompress(raw).decode())
    if d.get("code") != 0:
        raise RuntimeError("%s %s" % (api, d.get("msg")))
    dd = d.get("data") or {}
    return dd.get("fields") or [], dd.get("items") or []

def default_watch():
    from rotation_universe import SUB_UNIVERSE, _norm
    out = set()
    p = os.path.join(DATA, "risk_watch.txt")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            s = line.strip()
            if s and not s.startswith("#"):
                out.add(s.split()[0])
    # DB: 细分宇宙成员(东财概念成分) + 持仓
    try:
        import psycopg2 as _pg
        conn = _pg.connect(os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
        cur = conn.cursor()
        cur.execute("select concept_name, ts_code from stock_concept_map")
        for cname, code in cur.fetchall():
            if any(any(_norm(k) in _norm(cname) for k in kws) for kws in SUB_UNIVERSE.values()):
                out.add(code)
        try:
            cur.execute("select ts_code from paper_positions")
            for r in cur.fetchall():
                if r[0]: out.add(r[0])
        except Exception:
            pass
        cur.close(); conn.close()
    except Exception as e:
        print("DB watch err", e, flush=True)
    # rotation_crowding top 名单
    cp = os.path.join(DATA, "rotation_crowding.json")
    if os.path.exists(cp):
        try:
            d = json.load(open(cp, encoding="utf-8"))
            for key in ("chip_top", "optics_top"):
                for it in d.get(key) or []:
                    if isinstance(it, dict) and it.get("symbol"): out.add(it["symbol"])
        except Exception:
            pass
    return sorted(out)

def fetch_flags(symbol):
    flags = []
    # forecast
    try:
        _, items = call("forecast", {"ts_code": symbol},
                        "ts_code,ann_date,end_date,type,net_profit_min,net_profit_max,p_change_min,p_change_max")
    except Exception as e:
        print("forecast err", symbol, str(e)[:80], flush=True); items = []
    nowy = int(time.strftime("%Y"))
    def fresh(ann):
        try:
            y = int(str(ann)[:4]); age = (time.time() - time.mktime(time.strptime(str(ann), "%Y%m%d"))) / 86400.0
            return y == nowy or age <= 200
        except Exception:
            return False
    items = [it for it in items if len(it) > 1 and fresh(str(it[1]))]
    if items:
        latest = max(items, key=lambda it: (str(it[2] or ""), str(it[1] or "")))
        try:
            ftype = str(latest[3] or ""); ann = str(latest[1] or "")
            endd = str(latest[2] or "")
            pcmax = None
            try:
                pcmax = float(latest[7]) if latest[7] is not None else None
            except Exception:
                pass
            bad = ftype in BAD_TYPES or ((ftype in ("预减", "略减")) and pcmax is not None and pcmax < -30)
            if bad:
                flags.append(("earnings_bad", ftype + " pchg_max=" + str(pcmax), ann, "forecast"))
            else:
                flags.append(("earnings_clear", ftype + " pchg_max=" + str(pcmax), ann, "forecast"))
        except Exception as e:
            print("parse forecast err", symbol, str(e)[:60], flush=True)
    # express 仅当无 forecast 兜底
    if not items:
        try:
            _, items2 = call("express", {"ts_code": symbol}, "ts_code,ann_date,end_date,revenue")
            if items2:
                latest = max(items2, key=lambda it: (str(it[2] or ""), str(it[1] or "")))
                flags.append(("earnings_clear", "express revenue=%s" % (str(latest[3])), str(latest[1] or ""), "express"))
        except Exception as e:
            print("express err", symbol, str(e)[:60], flush=True)
    return flags

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", default="")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    stocks = [s.strip() for s in args.stocks.split(",") if s.strip()]
    if not stocks:
        stocks = default_watch()
    if args.limit > 0:
        stocks = stocks[:args.limit]
    print("watch stocks:", len(stocks), flush=True)
    conn = psycopg2.connect(DB); conn.autocommit = True
    cur = conn.cursor()
    # 全市场 ST 批量入旗(免逐股API)
    cur.execute("SELECT ts_code, is_st FROM stock_pool WHERE is_st IS NOT NULL AND is_st <> 0")
    st_all = cur.fetchall()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for sym, _v in st_all:
        cur.execute("INSERT INTO risk_flags (symbol,flag_type,value,ann_date,source,updated_at) VALUES (%s,'is_st','1','','stock_pool',%s)"
                    " ON CONFLICT (symbol,flag_type,source) DO UPDATE SET value='1', updated_at=EXCLUDED.updated_at", (sym, now))
    print("ST bulk flags:", len(st_all), flush=True)
    # stock_pool is_st for watch
    cur.execute("select ts_code, is_st from stock_pool where ts_code = ANY(%s)", (stocks,))
    pool = {r[0]: r[1] for r in cur.fetchall()}
    total = 0
    for i, sym in enumerate(stocks, 1):
        rows = [("is_st", str(pool.get(sym) or 0), "", "stock_pool")] if sym in pool else []
        for f in fetch_flags(sym):
            rows.append(f)
        cur.execute("DELETE FROM risk_flags WHERE symbol=%s", (sym,))  # 重算覆盖, 清掉过期/陈旧行
        for flag_type, value, ann, source in rows:
            cur.execute("INSERT INTO risk_flags (symbol,flag_type,value,ann_date,source,updated_at) VALUES (%s,%s,%s,%s,%s,%s)"
                        " ON CONFLICT (symbol,flag_type,source) DO UPDATE SET value=EXCLUDED.value, ann_date=EXCLUDED.ann_date, updated_at=EXCLUDED.updated_at",
                        (sym, flag_type, str(value)[:120], ann, source, now))
            total += 1
        if i % 20 == 0:
            print("progress", i, "/", len(stocks), flush=True)
        time.sleep(0.1)
    cur.close(); conn.close()
    print("DONE stocks=%d flags=%d" % (len(stocks), total), flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
