# -*- coding: utf-8 -*-
"""wolf_weekend_hedge.py — G9 周末/长假前避险（**盘中** 14:31 判定）。

狼大原文（NGA **2026-08-21**，逐字）:
  14:20「**2点半 如果还是缩量 还是不拉升 我会先把这两天T进去的仓位出来一半 防止周末出利空
         这样周一再拿回来。 出于仓位安全考虑 65%仓位过周末。**」
  14:35「2点半过了 **我按刚才说的操作了**。」

只有「周末/长假前最后一个交易日」才有意义；非节前直接跳过（省一次取数）。
产出 `wolf_weekend_hedge.json` → `wolf_discipline.discipline_context` 注入。
开关 `WOLF_WEEKEND_HEDGE`（默认 0=关）。

用法: python jobs/wolf_weekend_hedge.py [--time 14:30] [--dry-run]
"""
import os
import sys

for _p in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)


def main():
    dry = "--dry-run" in sys.argv
    hhmm = None
    if "--time" in sys.argv:
        try:
            hhmm = sys.argv[sys.argv.index("--time") + 1]
        except Exception:
            hhmm = None
    try:
        from app.services.wolf_weekend_hedge import enabled, run, recent_trade_days, is_pre_break_day, cutoff
        from app.services.wolf_eod import is_trade_day, today8
    except Exception as e:
        print(f"[weekend_hedge] 导入失败: {e}")
        return 1
    if not enabled():
        print("[weekend_hedge] WOLF_WEEKEND_HEDGE=0 → 跳过")
        return 0
    d = today8()
    if is_trade_day(d) is False:
        print(f"[weekend_hedge] {d} 非交易日 → 跳过")
        return 0
    pre = is_pre_break_day(d, recent_trade_days())
    if not pre.get("is_pre_break"):
        print(f"[weekend_hedge] {d} 不是周末/长假前最后一个交易日（{pre.get('reason') or pre.get('gap_days')}）→ 跳过")
        return 0
    res = run(save=not dry, hhmm=hhmm or cutoff())
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
