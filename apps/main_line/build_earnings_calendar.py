# -*- coding: utf-8 -*-
"""build_earnings_calendar.py — 业绩披露日历 v1（E05/E11/E14 型日历门数据）

输入: watch = 持仓 + rotation top/risk_watch(可 --stocks 指定)
数据: tushare forecast(业绩预告, 全历史) / express 兜底 → (end_date, ann_date, type)
截止规则(法定期限, 无精确披露日时的最迟日):
  Q1(0331)<=0430; 半年(0630)<=0831; Q3(0930)<=1031; 年报(1231)<=次年0430
输出: PG earnings_calendar（PK ref_date,symbol,end_date；不再写本地 JSON）
用法: python -u apps/main_line/build_earnings_calendar.py [--stocks X,Y] [--as-of 20260422]
"""

# ── DSN 收口（scripts/migrate_hardcoded_dsn.py 自动插入，见 core/dsn.py）──
import os as _os_dsn, sys as _sys_dsn
_ROOT_DSN = _os_dsn.path.dirname(_os_dsn.path.dirname(_os_dsn.path.dirname(_os_dsn.path.abspath(__file__))))
if _ROOT_DSN not in _sys_dsn.path:
    _sys_dsn.path.insert(0, _ROOT_DSN)
from core import dsn as _dsn_src  # noqa: E402
import os, sys, json, time, argparse
from datetime import date, datetime


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "apps"))
try:
    import psycopg2
except Exception:
    psycopg2 = None
DB = _dsn_src.main()
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
    except ImportError as _e_sil1:
        _silent_alert("build_earnings_calendar.py:35", _e_sil1)
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
    except Exception as _e_sil2:
        _silent_alert("build_earnings_calendar.py:53", _e_sil2)
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
    except Exception as _e_sil3:
        _silent_alert("build_earnings_calendar.py:70", _e_sil3)
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
        except Exception as _e_sil4:
            _silent_alert("build_earnings_calendar.py:88", _e_sil4)
    return sorted([x for x in out if x["ann"]], key=lambda x: x["ann"])

def fetch_disclosure(sym):
    out = {}
    for end in RECENT_ENDS:
        try:
            for it in call("disclosure_date", {"ts_code": sym, "end_date": end}, "ts_code,ann_date,end_date,pre_date,actual_date"):
                if len(it) >= 5:
                    out[str(it[2] or "")] = {"ann_date": str(it[1] or ""), "pre_date": str(it[3] or ""), "actual_date": str(it[4] or "")}
        except Exception as _e_sil5:
            _silent_alert("build_earnings_calendar.py:99", _e_sil5)
            continue
    return out

SCHEMA = """
CREATE TABLE IF NOT EXISTS earnings_calendar (
  ref_date DATE NOT NULL,
  symbol VARCHAR(16) NOT NULL,
  name VARCHAR(64),
  end_date DATE,
  ann_date DATE,
  pre_date DATE,
  actual_date DATE,
  type VARCHAR(32),
  source VARCHAR(32),
  disclosed BOOLEAN DEFAULT FALSE,
  bad BOOLEAN DEFAULT FALSE,
  deadline DATE,
  days_to_deadline INTEGER,
  updated_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (ref_date, symbol, end_date)
)
"""

UPSERT = """
INSERT INTO earnings_calendar
(ref_date, symbol, name, end_date, ann_date, pre_date, actual_date, type, source,
 disclosed, bad, deadline, days_to_deadline)
VALUES %s
ON CONFLICT (ref_date, symbol, end_date) DO UPDATE SET
  name=EXCLUDED.name, ann_date=EXCLUDED.ann_date, pre_date=EXCLUDED.pre_date,
  actual_date=EXCLUDED.actual_date, type=EXCLUDED.type, source=EXCLUDED.source,
  disclosed=EXCLUDED.disclosed, bad=EXCLUDED.bad, deadline=EXCLUDED.deadline,
  days_to_deadline=EXCLUDED.days_to_deadline, updated_at=now()
"""


def _d(v):
    v = str(v or "").strip()
    if len(v) != 8 or not v.isdigit():
        return None
    return v[:4] + "-" + v[4:6] + "-" + v[6:8]


def save_pg(ref: str, rows: list) -> int:
    if psycopg2 is None:
        raise RuntimeError("psycopg2 不可用")
    conn = psycopg2.connect(DB)
    try:
        cur = conn.cursor()
        cur.execute(SCHEMA)
        from psycopg2.extras import execute_values
        vals = []
        for r in rows:
            if not _d(r.get("end_date")):
                continue
            vals.append((_d(ref), r["symbol"], r.get("name") or "", _d(r.get("end_date")),
                         _d(r.get("ann_date")), _d(r.get("pre_date")), _d(r.get("actual_date")),
                         r.get("type") or "", r.get("source") or "", bool(r.get("disclosed")),
                         bool(r.get("bad")), _d(r.get("deadline")), r.get("days_to_deadline")))
        if vals:
            execute_values(cur, UPSERT, vals)
        conn.commit()
        return len(vals)
    finally:
        conn.close()


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
        except Exception as _e_sil6:
            _silent_alert("build_earnings_calendar.py:126", _e_sil6)
        rows.append({"symbol": sym, "name": name_of(sym), "end_date": latest["end"], "ann_date": latest["ann"],
                     "pre_date": pre_date, "actual_date": actual_date, "type": latest["type"],
                     "source": latest["src"], "disclosed": disclosed, "bad": bad,
                     "deadline": dline, "days_to_deadline": dd})
    n = save_pg(ref, rows)
    print("UPSERT earnings_calendar ref=%s rows=%s" % (ref, n))
    undisclosed = [r for r in rows if not r["disclosed"]]
    print("undisclosed", len(undisclosed))
    for r in undisclosed[:15]:
        print(r["symbol"], r["name"], "end", r["end_date"], "type", r["type"], "deadline", r["deadline"], "dd", r["days_to_deadline"])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
