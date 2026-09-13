# -*- coding: utf-8 -*-
"""backfill_market_bars_promax.py — 用 **promax** 源回填 `mkt_bars_daily`（gzcloud token 已失效时的替代）。

背景（2026-09-13）：`TUSHARE_API_URL=https://ts.gyzcloud.top/api` 的 token 报
「**Token无效或已过期，请联系客服续费**」→ 2025 回填 0 行。实测 **promax**（`PROMAX_URL` + `X-API-Key`）
的 `/daily`、`/daily_basic`、`/trade_cal` 均 200 可用 → 改走它。

用法：
    python jobs/backfill_market_bars_promax.py --start 20250101 --end 20251231 [--sleep 1.0]
    python jobs/backfill_market_bars_promax.py --coverage
特性：①已存在的交易日自动跳过（断点续跑）；②502/503/504/超时退避重试；③每天一次 `/daily`（必要时补 `/daily_basic`）。

注意（2026-09-13 起）：日常取数已统一走 `core/tushare_relay.py`（**datahubco 基础接口优先**(RDS 快) +
promax 兜底），`jobs/backfill_market_bars.py` 即为该路径；本脚本保留纯 promax 通道，仅在中继不可用
或需要绕开 datahubco 时使用。
"""
import os
import sys
import time

for _p in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

PROMAX_URL = os.getenv("PROMAX_URL", "https://pcd.mobcvb.cn/tushare/pro")
TIMEOUT = float(os.getenv("PROMAX_TIMEOUT", "30"))
ATTEMPTS = int(os.getenv("PROMAX_ATTEMPTS", "6"))     # promax 抖动频繁（502/503/504/断连），多试几次


def _headers():
    k = (os.getenv("PROMAX_API_KEY") or "").strip()
    if not k:
        raise EnvironmentError("PROMAX_API_KEY 未配置")
    return {"X-API-Key": k}


def pm_get(path, **params):
    """GET promax，带退避重试（502/503/504 与超时是常态抖动）。"""
    import requests
    last = None
    for k in range(ATTEMPTS):
        try:
            r = requests.get(PROMAX_URL + path, params=params or None, headers=_headers(), timeout=TIMEOUT)
            if r.status_code in (502, 503, 504):
                raise RuntimeError("HTTP %s" % r.status_code)
            r.raise_for_status()
            j = r.json()
            if j.get("code") not in (0, None):
                raise RuntimeError("code=%s msg=%s" % (j.get("code"), str(j.get("msg"))[:60]))
            return (j.get("data") or {}).get("items") or []
        except Exception as e:
            last = e
            time.sleep(2.5 * (k + 1))
    raise last


def _f(v):
    try:
        return None if v in (None, "", "None") else float(v)
    except (TypeError, ValueError):
        return None


def trade_days(start8, end8):
    items = pm_get("/trade_cal", exchange="SSE", start_date=start8, end_date=end8, is_open="1")
    return sorted(str(x[1]) for x in items if str(x[2]) == "1")


def day_rows_promax(d8):
    """取某日全市场日线（promax /daily 字段：ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount,...）"""
    items = pm_get("/daily", trade_date=d8)
    if not items:
        return []
    rows = []
    for it in items:
        # 位置解析：ts_code(0) trade_date(1) open(2) high(3) low(4) close(5) pre_close(6) change(7) pct_chg(8) vol(9) amount(10)
        if len(it) < 11:
            continue
        ts = str(it[0])
        if not ts:
            continue
        rows.append({"ts_code": ts, "trade_date": d8,
                     "open": _f(it[2]), "high": _f(it[3]), "low": _f(it[4]), "close": _f(it[5]),
                     "pre_close": _f(it[6]), "pct_chg": _f(it[8]),
                     "vol": _f(it[9]), "amount": _f(it[10]),
                     "total_mv": None, "turnover_rate": None, "is_st": None})
    return rows


def enrich_basic(d8, rows):
    """补 total_mv / turnover_rate（可选；失败不影响主流程）。"""
    try:
        items = pm_get("/daily_basic", trade_date=d8,
                       fields="ts_code,total_mv,turnover_rate")
        m = {}
        for it in items:
            if len(it) >= 3:
                m[str(it[0])] = (it[1], it[2])
        hit = 0
        for r in rows:
            v = m.get(r["ts_code"])
            if v:
                r["total_mv"], r["turnover_rate"] = _f(v[0]), _f(v[1])
                hit += 1
        return hit
    except Exception as e:
        print("[pm] daily_basic(%s) 跳过: %s" % (d8, str(e)[:60]), flush=True)
        return 0


def main():
    argv = sys.argv
    from app.services.mkt_bars import coverage, ensure_table, upsert_day
    if "--coverage" in argv:
        print(coverage())
        return 0
    start = argv[argv.index("--start") + 1] if "--start" in argv else "20250101"
    end = argv[argv.index("--end") + 1] if "--end" in argv else start
    sleep_sec = float(argv[argv.index("--sleep") + 1]) if "--sleep" in argv else 1.0
    basic = "--basic" in argv

    from app.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    ensure_table(db)
    have = {r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM mkt_bars_daily WHERE trade_date >= :a AND trade_date <= :b"),
        {"a": start, "b": end})}
    ds = trade_days(start, end)
    todo = [d for d in ds if d not in have]
    print("[pm] 期间 %s→%s 共 %d 个交易日，已有 %d，待回填 %d" % (start, end, len(ds), len(have), len(todo)),
          flush=True)
    tot, errs = 0, []
    for i, d8 in enumerate(todo, 1):
        try:
            rows = day_rows_promax(d8)
            if not rows:
                print("[pm] %d/%d %s 无数据" % (i, len(todo), d8), flush=True)
                continue
            if basic:
                enrich_basic(d8, rows)
            n = upsert_day(db, rows)
            db.commit()
            tot += n
            if i % 10 == 0 or i == len(todo):
                print("[pm] %d/%d %s 写入 %d（累计 %d）" % (i, len(todo), d8, n, tot), flush=True)
        except Exception as e:
            errs.append("%s:%s" % (d8, str(e)[:60]))
            print("[pm] %d/%d %s 失败 %s: %s" % (i, len(todo), d8, type(e).__name__, str(e)[:70]), flush=True)
            try:
                db.rollback()
            except Exception:
                pass
        time.sleep(max(0.0, sleep_sec))
    print("[pm] 汇总: 写入 %d 行，失败 %d 天 %s" % (tot, len(errs), errs[:3]), flush=True)
    print("[pm] 覆盖度:", coverage(), flush=True)
    db.close()
    return 0 if not errs else 1


if __name__ == "__main__":
    sys.exit(main())
