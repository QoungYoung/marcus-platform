# -*- coding: utf-8 -*-
"""wolf_theme_resilience.py — 盘后算「跌得少、弹得早」的方向（C2）。

狼大 2026-01-27（调整期第二步）「大家跌我跌少一点，大家反弹我抢先反弹」这种方向 →
盘后产出方向优先级，供次日/调整期参考（提示层，不改选股）。

用法: python jobs/wolf_theme_resilience.py [--days N] [--dry-run]
"""
import os
import sys

for _p in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)


def main():
    dry = "--dry-run" in sys.argv
    days = None
    if "--days" in sys.argv:
        try:
            days = int(sys.argv[sys.argv.index("--days") + 1])
        except Exception:
            days = None
    try:
        from app.services.wolf_theme_resilience import enabled, run
        from app.services.wolf_eod import gate
    except Exception as e:
        print(f"[resilience] 导入失败: {e}")
        return 1
    if not enabled():
        print("[resilience] WOLF_THEME_RESILIENCE=0 → 跳过")
        return 0
    # ── EOD 就绪守卫（2026-09-11）：盘后源要过一段时间才更新，未就绪就有限等待，
    #    仍没就绪则**非 0 退出交给调度器重试**（不要"假装成功"——那正是静默失效的来源）
    _g = gate(date8)
    if _g == 1:
        return 0
    if _g == 2:
        return 2
    res = run(days=days, save=not dry)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
