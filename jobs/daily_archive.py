# -*- coding: utf-8 -*-
"""daily_archive.py — G2 每日存档（盘后 19:50，紧跟在决策对象之后）。

把当天**全部产物**（含 concept_long / theme_inst_flow / etf_share_flow / main_line_state /
stock_confirm_result 这些**当日覆盖型**文件）快照到 `data/_archive/<date>/`，并写 manifest
（来源、大小、sha256、缺哪些）。从此不再出现"历史只能靠回放猜"。
开关 `WOLF_DAILY_ARCHIVE`（默认 0）。

用法: python jobs/daily_archive.py [--date YYYYMMDD] [--skip-db]
"""
import os
import sys

for _p in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)


def main():
    d8 = None
    if "--date" in sys.argv:
        try:
            d8 = sys.argv[sys.argv.index("--date") + 1]
        except Exception:
            d8 = None
    try:
        from app.services.daily_archive import enabled, run
        from app.services.wolf_eod import gate
    except Exception as e:
        print(f"[archive] 导入失败: {e}")
        return 1
    if not enabled():
        print("[archive] WOLF_DAILY_ARCHIVE=0 → 跳过")
        return 0
    _g = gate(d8)
    if _g == 1:
        return 0
    if _g == 2:
        return 2
    res = run(d8=d8, save=True, skip_db=("--skip-db" in sys.argv))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
