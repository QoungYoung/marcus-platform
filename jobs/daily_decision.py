# -*- coding: utf-8 -*-
"""daily_decision.py — G1 每日决策对象（盘后 19:45，先于存档）。

把当天 L1 方向 / L2 档位 / L3 仓位 / L4 选票 / L5 买点 / L6 兑现汇成
`data/decision/<date>.json`，供次日盘中所有腿引用（`daily_decision.entry_allowed()`）。
开关 `WOLF_DAILY_DECISION`（默认 0）。

用法: python jobs/daily_decision.py [--date YYYYMMDD] [--dry-run]
"""
import os
import sys

for _p in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)


def main():
    dry = "--dry-run" in sys.argv
    d8 = None
    if "--date" in sys.argv:
        try:
            d8 = sys.argv[sys.argv.index("--date") + 1]
        except Exception:
            d8 = None
    try:
        from app.services.daily_decision import enabled, run
        from app.services.wolf_eod import gate
    except Exception as e:
        print(f"[decision] 导入失败: {e}")
        return 1
    if not enabled():
        print("[decision] WOLF_DAILY_DECISION=0 → 跳过")
        return 0
    _g = gate(d8)
    if _g == 1:
        return 0
    if _g == 2:
        return 2
    res = run(d8=d8, save=not dry)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
