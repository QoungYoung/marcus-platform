# -*- coding: utf-8 -*-
"""backfill_market_bars.py — 从 tushare 回填回测用行情表（mkt_bars_daily）。

用法: python jobs/backfill_market_bars.py --start 20260101 --end 20260911 [--sleep 0.4]
      python jobs/backfill_market_bars.py --coverage
"""
import os
import sys

for _p in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)


def main():
    argv = sys.argv
    if "--coverage" in argv:
        from app.services.mkt_bars import coverage
        print(coverage())
        return 0
    start = end = None
    for k, val in (("--start", "start"), ("--end", "end")):
        if k in argv:
            try:
                v = argv[argv.index(k) + 1]
                if val == "start":
                    start = v
                else:
                    end = v
            except Exception:
                pass
    sleep_sec = 0.4
    if "--sleep" in argv:
        try:
            sleep_sec = float(argv[argv.index("--sleep") + 1])
        except Exception:
            sleep_sec = 0.4
    import datetime as _dt
    start = start or "20260101"
    end = end or _dt.date.today().strftime("%Y%m%d")
    from app.services.mkt_bars import backfill, coverage
    print(f"[mkt_bars] 回填 {start} → {end}（sleep={sleep_sec}s）", flush=True)
    res = backfill(start, end, sleep_sec=sleep_sec, save=True)
    print("[mkt_bars] 汇总:", {k: res.get(k) for k in ("ok", "days", "rows", "skipped")},
          "错误:", (res.get("errors") or [])[:3], flush=True)
    print("[mkt_bars] 覆盖度:", coverage(), flush=True)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
