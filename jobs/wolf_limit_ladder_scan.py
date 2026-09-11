# -*- coding: utf-8 -*-
"""wolf_limit_ladder_scan.py — 盘后跑涨停梯队/连板结构（B4）。

狼大 2025-04-21 的复盘流程是在**盘后**做的（「看第10的涨停板方向…判断板块的强弱
从而推断出接下来要做的方向」），且 tushare `limit_list_d` 本身是 **EOD 数据**
（实测当日盘中返回 0 行）→ 本任务排 **收盘后**。

产出：DATA_DIR/wolf_limit_ladder.json（次日盘前/决策上下文可读）。

用法: python jobs/wolf_limit_ladder_scan.py [YYYYMMDD] [--dry-run]
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
        from app.services.wolf_limit_ladder import enabled, scan_and_save
        from app.services.wolf_eod import gate
    except Exception as e:
        print(f"[limit_ladder] 导入失败: {e}")
        return 1
    if not enabled():
        print("[limit_ladder] WOLF_LIMIT_LADDER=0 → 跳过")
        return 0
    # ── EOD 就绪守卫（2026-09-11）：盘后源要过一段时间才更新，未就绪就有限等待，
    #    仍没就绪则**非 0 退出交给调度器重试**（不要"假装成功"——那正是静默失效的来源）
    _g = gate(date8)
    if _g == 1:
        return 0
    if _g == 2:
        return 2
    res = scan_and_save(date8=date8, dry_run=dry)
    if not res.get("ok"):
        print(f"[limit_ladder] 未产出: {res}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
