# -*- coding: utf-8 -*-
"""wolf_index_futures.py — 盘后算股指期货多空 → 次日黄白线预判（A3）。

狼大 2025-04-15 条件1「看一下A50期货 沪深300期货 科创50期货的多单和空单的变化
(以此分辨次日开盘是黄线还是白线在上)」；口径按他 2026-01-23「不是当日空单和多单相比
是和多空前一日的增减对比」。

⚠️ 实测命中率约 51%（158 日）→ 只做提示，不参与硬门（见模块文档）。

用法: python jobs/wolf_index_futures.py [YYYYMMDD] [--dry-run]
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
        from app.services.wolf_index_futures import enabled, run
        from app.services.wolf_eod import gate
    except Exception as e:
        print(f"[index_futures] 导入失败: {e}")
        return 1
    if not enabled():
        print("[index_futures] WOLF_INDEX_FUTURES=0 → 跳过")
        return 0
    _g = gate(date8)
    if _g == 1:
        return 0
    if _g == 2:
        return 2
    res = run(date8=date8, save=not dry)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
