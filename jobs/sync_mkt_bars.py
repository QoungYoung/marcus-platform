# -*- coding: utf-8 -*-
"""sync_mkt_bars.py — 日线底座（PG `mkt_bars_daily`）**增量同步 + 自愈**（2026-09-15）。

背景（用户 2026-09-15 指示）：
  · 诊断发现 PG `mkt_bars_daily` 停在 **20260911**，而 09-14 是交易日 —— 中继取数是好的
    （`relay_items("daily", trade_date="20260914")` → 5550 行 / 2.8 秒），**缺的是"没人去取"**：
    `config/tasks.yaml` 里 `mkt_bars`/`backfill` 各出现 0 次，而 `app/services/mkt_bars.py` 的回填能力早就齐备。
  · 用户要求：① 补回填；② 合并进现有"收盘后数据刷新"任务（不再新开任务）；
    ③ **以后再遇到库里没有新数据，就直接调 relay 取并落库**（自愈）。

用法::

    python jobs/sync_mkt_bars.py                # 自愈：把缺的交易日补到"最近已收盘交易日"
    python jobs/sync_mkt_bars.py --dry          # 只看缺哪几天，不写库
    python jobs/sync_mkt_bars.py --start 20260911 --end 20260915
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = "/app" if os.path.isdir("/app/app") else str(Path(__file__).resolve().parent.parent)
for _p in (ROOT, os.path.join(ROOT, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _db_max_date():
    from app.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        r = db.execute(text("SELECT max(trade_date) FROM mkt_bars_daily")).fetchone()
        return str(r[0]) if r and r[0] else None
    finally:
        db.close()


def _closed_trade_days(start8, end8):
    """[start8, end8] 内**已收盘**的交易日（今天盘中不算今日）。"""
    import datetime as _dt
    from app.services import mkt_bars as MB
    days = MB.trade_days(start8, end8) or []
    now = _dt.datetime.now()
    today = now.strftime("%Y%m%d")
    closed_today = (now.hour * 100 + now.minute) >= 1530     # 收盘后含当天
    return [d for d in days if d < today or (closed_today and d == today)]


def sync(start8=None, end8=None, dry=False, lookback_days=15):
    """返回 {max_before, missing, rows, ok}；缺哪几天就补哪几天。"""
    import datetime as _dt
    from app.services import mkt_bars as MB
    today = _dt.date.today()
    if not end8:
        end8 = today.strftime("%Y%m%d")
    if not start8:
        start8 = (today - _dt.timedelta(days=int(lookback_days))).strftime("%Y%m%d")

    max_before = _db_max_date()
    days = _closed_trade_days(start8, end8)
    missing = [d for d in days if not max_before or d > max_before]
    print("[sync_mkt_bars] 库内最新=%s | 区间 %s→%s 内已收盘交易日 %d 天 | 缺 %d 天: %s"
          % (max_before, start8, end8, len(days), len(missing), missing))
    if not missing:
        return {"ok": True, "max_before": max_before, "missing": [], "rows": 0, "dry": bool(dry)}
    if dry:
        return {"ok": True, "max_before": max_before, "missing": missing, "rows": 0, "dry": True}
    res = MB.backfill(start8, end8, days=missing)
    print("[sync_mkt_bars] 回填结果: rows=%s skipped=%s errors=%s"
          % (res.get("rows"), res.get("skipped"), res.get("errors")[:3]))
    return {"ok": bool(res.get("ok", True)), "max_before": max_before, "missing": missing,
            "rows": res.get("rows", 0), "errors": res.get("errors", []), "dry": False}


def ensure_fresh(max_lag_days=2, quiet=True):
    """委托 `app.services.mkt_bars.ensure_fresh`（单一实现；资金门/刷新链都调它）。"""
    from app.services import mkt_bars as MB
    return MB.ensure_fresh(max_lag_days=max_lag_days, quiet=quiet)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--lookback-days", type=int, default=15)
    args = ap.parse_args()
    r = sync(args.start, args.end, dry=args.dry, lookback_days=args.lookback_days)
    try:
        from app.services import mkt_bars as MB
        print("[sync_mkt_bars] 覆盖率:", MB.coverage())
    except Exception as e:
        print("[sync_mkt_bars] coverage 查询失败:", str(e)[:80])
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
