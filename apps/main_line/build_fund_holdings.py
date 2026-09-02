# -*- coding: utf-8 -*-
"""build_fund_holdings.py — 真实公募持仓(Q2 20260630)回填 postgres fund_portfolio_holdings
数据源: gyzcloud TUSHARE_API_URL fund_share(选规模topN) + fund_portfolio(每基金全部季度, 取 end_date=20260630)
用法: python -u apps/main_line/build_fund_holdings.py [top_n] [end_date]
"""
import os, sys, json, urllib.request, gzip, time
try:
    import psycopg2
except Exception as e:
    print("ERR psycopg2:", e); sys.exit(1)

TOKEN = os.getenv("TUSHARE_TOKEN", "")
URL = os.getenv("TUSHARE_API_URL", "https://ts.gyzcloud.top/api")
DB = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
def _prev_quarter_end(d=None):
    from datetime import date as _date
    d = d or _date.today()
    y, m = d.year, d.month
    if m <= 3: return "%d1231" % (y - 1)
    if m <= 6: return "%d0331" % y
    if m <= 9: return "%d0630" % y
    return "%d0930" % y

def _latest_share_date():
    from datetime import date as _date, timedelta as _td
    for back in range(0, 8):
        ds = (_date.today() - _td(days=back)).strftime("%Y%m%d")
        try:
            it = call("fund_share", {"trade_date": ds}, "ts_code,trade_date,fd_share")
        except Exception:
            continue
        if it: return ds
    return "20260827"

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
    return d.get("data", {}).get("items") or []

def main():
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    end = sys.argv[2] if len(sys.argv) > 2 else _prev_quarter_end()
    share_date = _latest_share_date()
    print("share_date:", share_date, "end:", end, flush=True)
    try:
        share = call("fund_share", {"trade_date": share_date}, "ts_code,trade_date,fd_share")
    except Exception as e:
        print("fund_share ERR", e); return 1
    best = {}
    for row in share:
        try:
            code = row[0]; sh = float(row[2] or 0)
        except Exception:
            continue
        if sh > best.get(code, 0): best[code] = sh
    funds = sorted(best, key=lambda c: best[c], reverse=True)[:top_n]
    print("top funds:", len(funds), "first:", funds[:5], flush=True)
    conn = psycopg2.connect(DB)
    conn.autocommit = True
    cur = conn.cursor()
    total = 0; done = 0; err = 0
    for i, f in enumerate(funds, 1):
        try:
            rows = call("fund_portfolio", {"ts_code": f},
                        "ts_code,ann_date,end_date,symbol,mkv,amount,stk_mkv_ratio,stk_float_ratio")
        except Exception as e:
            err += 1
            print("skip", f, str(e)[:80], flush=True)
            continue
        keep = [r for r in rows if len(r) >= 8 and str(r[2]) == end]
        for r in keep:
            cur.execute(
                "INSERT INTO fund_portfolio_holdings (fund_code,ann_date,end_date,symbol,mkv,amount,stk_mkv_ratio,stk_float_ratio)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (fund_code,symbol,end_date) DO UPDATE SET ann_date=EXCLUDED.ann_date,"
                " mkv=EXCLUDED.mkv, amount=EXCLUDED.amount, stk_mkv_ratio=EXCLUDED.stk_mkv_ratio, stk_float_ratio=EXCLUDED.stk_float_ratio",
                (str(r[0]), str(r[1]) if r[1] else None, str(r[2]), str(r[3]),
                 float(r[4]) if r[4] is not None else None, float(r[5]) if r[5] is not None else None,
                 float(r[6]) if r[6] is not None else None, float(r[7]) if r[7] is not None else None))
            total += 1
        done += 1
        if i % 10 == 0:
            print("progress", i, "/", len(funds), "rows", total, flush=True)
        time.sleep(0.3)
    cur.close(); conn.close()
    print("DONE funds=%d rows=%d err=%d end=%s" % (done, total, err, end), flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
