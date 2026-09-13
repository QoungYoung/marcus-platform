# -*- coding: utf-8 -*-
"""build_earnings_calendar.py — 业绩披露日历 v1（E05/E11/E14 型日历门数据）

输入: watch = 持仓 + rotation top/risk_watch(可 --stocks 指定)
数据: tushare forecast(业绩预告, 全历史) / express 兜底 → (end_date, ann_date, type)
截止规则(法定期限, 无精确披露日时的最迟日):
  Q1(0331)<=0430; 半年(0630)<=0831; Q3(0930)<=1031; 年报(1231)<=次年0430
输出: data/earnings_calendar.json {ref_date, rows:[{symbol,name,end_date,ann_date,type,disclosed,bad,deadline,days_to_deadline}], undisclosed:[...]}
用法: python -u apps/main_line/build_earnings_calendar.py [--stocks X,Y] [--as-of 20260422]
"""
import os, sys, json, time, argparse
from datetime import date, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "apps"))
try:
    import psycopg2
except Exception:
    psycopg2 = None
DB = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
DATA = os.environ.get("DATA_DIR", "data")
# 2026-09-13: 取数走 core/tushare_relay.py（datahubco+promax），旧 TUSHARE_API_URL 已废弃

RECENT_ENDS = ['20250630','20250930','20251231','20260331','20260630','20260930']
DEADLINE = {("03", "31"): ("04", "30"), ("06", "30"): ("08", "31"),
            ("09", "30"): ("10", "31"), ("12", "31"): ("04", "30")}
BAD = {"首亏", "续亏"}

def _relay():
    """加载 core/tushare_relay.py（2026-09-13 起 datahubco 基础接口 + promax 聚合接口，
    替代已失效的 gzcloud 代理）。"""
    import importlib, pathlib, sys
    try:
        return importlib.import_module("tushare_relay")
    except ImportError:
        pass
    for _p in pathlib.Path(__file__).resolve().parents:
        if (_p / "core" / "tushare_relay.py").exists():
            sys.path.insert(0, str(_p / "core"))
            return importlib.import_module("tushare_relay")
    raise ImportError("core/tushare_relay.py 未找到")


def call(api, params, fields=""):
    """Tushare 中继查询（返回 items 行列表；中继内部含重试/分页/双源降级）。"""
    _fields, items = _relay().relay_items(api, fields=fields, **(params or {}))
    return items or []
def watch():
    out = set()
    try:
        from build_risk_flags import default_watch
        out |= set(default_watch())
    except Exception:
        pass
    return sorted(out)

def deadline_for(end):
    try:
        e = str(end); return e[:4] + DEADLINE[(e[4:6], e[6:8])][0] + DEADLINE[(e[4:6], e[6:8])][1]
    except Exception:
        return ""

def name_of(sym):
    try:
        if psycopg2:
            conn = psycopg2.connect(DB); cur = conn.cursor()
            cur.execute("SELECT name FROM stock_pool WHERE ts_code=%s", (sym,))
            r = cur.fetchone(); cur.close(); conn.close()
            return str(r[0]) if r else ""
    except Exception:
        pass
    return ""

def fetch_one(sym):
    out = []
    try:
        for it in call("forecast", {"ts_code": sym}, "ts_code,ann_date,end_date,type,p_change_max"):
            if len(it) >= 4:
                out.append({"ann": str(it[1] or ""), "end": str(it[2] or ""), "type": str(it[3] or ""),
                            "pcmax": float(it[4]) if len(it) > 4 and it[4] is not None else None, "src": "forecast"})
    except Exception:
        out = []
    if not out:
        try:
            for it in call("express", {"ts_code": sym}, "ts_code,ann_date,end_date,revenue"):
                out.append({"ann": str(it[1] or ""), "end": str(it[2] or ""), "type": "express",
                            "pcmax": None, "src": "express"})
        except Exception:
            pass
    return sorted([x for x in out if x["ann"]], key=lambda x: x["ann"])

def fetch_disclosure(sym):
    out = {}
    for end in RECENT_ENDS:
        try:
            for it in call("disclosure_date", {"ts_code": sym, "end_date": end}, "ts_code,ann_date,end_date,pre_date,actual_date"):
                if len(it) >= 5:
                    out[str(it[2] or "")] = {"ann_date": str(it[1] or ""), "pre_date": str(it[3] or ""), "actual_date": str(it[4] or "")}
        except Exception:
            continue
    return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--stocks", default="")
    ap.add_argument("--as-of", default="")
    args = ap.parse_args()
    ref = args.as_of or time.strftime("%Y%m%d")
    stocks = [s.strip() for s in args.stocks.split(",") if s.strip()] or watch()
    rows = []
    for sym in stocks:
        hist = fetch_one(sym)
        if not hist:
            continue
        latest = hist[-1]
        disc = fetch_disclosure(sym)
        prec = disc.get(latest["end"]) or {}
        pre_date = prec.get("pre_date") or ""
        actual_date = prec.get("actual_date") or ""
        disclosed = bool((actual_date and actual_date <= ref) or (latest["ann"] and latest["ann"] <= ref))
        bad = bool(latest["type"] in BAD or (latest["type"] in ("预减", "略减") and latest.get("pcmax") is not None and latest["pcmax"] < -30))
        dline = actual_date or pre_date or deadline_for(latest["end"])
        dd = None
        try:
            if dline:
                dd = (datetime.strptime(dline, "%Y%m%d").date() - datetime.strptime(ref, "%Y%m%d").date()).days
        except Exception:
            pass
        rows.append({"symbol": sym, "name": name_of(sym), "end_date": latest["end"], "ann_date": latest["ann"],
                     "pre_date": pre_date, "actual_date": actual_date, "type": latest["type"],
                     "source": latest["src"], "disclosed": disclosed, "bad": bad,
                     "deadline": dline, "days_to_deadline": dd})
    out = {"ref_date": ref, "rows": rows,
           "undisclosed": [r for r in rows if not r["disclosed"]],
           "recently_disclosed": [r for r in rows if r["disclosed"]]}
    path = os.path.join(DATA, "earnings_calendar.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE", path, "rows", len(rows))
    print("undisclosed", len(out["undisclosed"]))
    for r in out["undisclosed"][:15]:
        print(r["symbol"], r["name"], "end", r["end_date"], "type", r["type"], "deadline", r["deadline"], "dd", r["days_to_deadline"])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
