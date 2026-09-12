# -*- coding: utf-8 -*-
"""backfill_research_reports.py — 按日回填全市场研报标题（catalyst 重建数据源）。

用法:
  python jobs/backfill_research_reports.py --start 20260101 --end 20260531 [--sleep 1.2]
  python jobs/backfill_research_reports.py --retry-failed     # 只重跑失败日期
  python jobs/backfill_research_reports.py --coverage
"""
import os
import sys

for _p in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)


def main():
    argv = sys.argv
    from app.services.research_reports import backfill, coverage
    if "--coverage" in argv:
        import json
        print(json.dumps(coverage(), ensure_ascii=False, indent=1))
        return 0
    sleep_sec = 1.2
    if "--sleep" in argv:
        try:
            sleep_sec = float(argv[argv.index("--sleep") + 1])
        except Exception:
            pass
    if "--retry-failed" in argv:
        cov = coverage()
        days = cov.get("failed_days") or []
        if not days:
            print("[rr] 没有失败日期")
            return 0
        print("[rr] 重跑失败日期 %d 天" % len(days), flush=True)
        res = backfill(days, sleep_sec=sleep_sec, save=True)
    else:
        start = end = None
        for k, which in (("--start", "s"), ("--end", "e")):
            if k in argv:
                try:
                    v = argv[argv.index(k) + 1]
                    if which == "s":
                        start = v
                    else:
                        end = v
                except Exception:
                    pass
        import datetime as _dt
        start = start or "20260101"
        end = end or _dt.date.today().strftime("%Y%m%d")
        # 交易日：优先用已有行情表里的日期（已回填 2026 全年），避免再打一次日历接口
        days = []
        try:
            from app.database import SessionLocal
            from sqlalchemy import text
            db = SessionLocal()
            try:
                rows = db.execute(text("SELECT DISTINCT trade_date FROM mkt_bars_daily "
                                       "WHERE trade_date BETWEEN :s AND :e ORDER BY 1"),
                                  {"s": start, "e": end}).mappings().all()
                days = [r["trade_date"] for r in rows]
            finally:
                db.close()
        except Exception as e:
            print("[rr] 取交易日失败: %s" % str(e)[:80])
        if not days:
            from app.services.mkt_bars import trade_days
            days = trade_days(start, end)
        print("[rr] 回填研报 %s → %s，共 %d 个交易日（sleep=%ss）" % (start, end, len(days), sleep_sec), flush=True)
        res = backfill(days, sleep_sec=sleep_sec, save=True)
    print("[rr] 汇总:", {k: res.get(k) for k in ("ok", "days", "ok_days", "empty_days", "rows")},
          "| 失败:", (res.get("failed_days") or [])[:10], flush=True)
    import json
    print("[rr] 覆盖度:", json.dumps(coverage(), ensure_ascii=False)[:400], flush=True)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
