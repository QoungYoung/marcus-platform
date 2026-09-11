# -*- coding: utf-8 -*-
"""wolf_review_score_run.py — 盘后跑「每日复盘打分表」（B1）。

狼大 2025-04-21「最后对比分数就行，至少对明天开盘是高开还是低开…做个提前的预判，并且做好计划，
然后第二天执行的过程中稍微根据当天行情调整就行」→ 打分在**盘后**做，产出供**次日盘前**用
（指数前 2 小时方向预判）。

用法: python jobs/wolf_review_score.py [YYYYMMDD] [--dry-run]
"""
import os
import sys

for _p in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    dry = "--dry-run" in sys.argv
    date8 = args[0] if args else None
    try:
        from app.services.wolf_review_score import enabled, run
        from app.services.wolf_eod import gate
    except Exception as e:
        print(f"[review_score] 导入失败: {e}")
        return 1
    if not enabled():
        print("[review_score] WOLF_REVIEW_SCORE=0 → 跳过")
        return 0
    # ── EOD 就绪守卫（2026-09-11）：盘后源要过一段时间才更新，未就绪就有限等待，
    #    仍没就绪则**非 0 退出交给调度器重试**（不要"假装成功"——那正是静默失效的来源）
    _g = gate(date8)
    if _g == 1:
        return 0
    if _g == 2:
        return 2
    res = run(date8=date8, save=not dry)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
