# -*- coding: utf-8 -*-
"""wolf_volume_gate.py — G10 量能门槛（盘后）。

狼大原文（NGA，逐条精读）:
  · 2026-09-03 14:44「这里**不上3WE的突破就是诱多** 简单直接的结论。」
  · 2026-08-20 13:51「**2WE是地量了** 只要放量就没事了。」
  · 2026-08-26 10:51「你看拉了指数1个点 量能只放了400E 而且是**2WE以下的微微放量**…那我也不会追。」

产出 `wolf_volume_gate.json` → 次日 `wolf_discipline.discipline_context` 注入「关口处量能是否够」。
开关 `WOLF_VOLUME_GATE`（默认 0=关）。

用法: python jobs/wolf_volume_gate.py [--days N] [--dry-run]
"""
import os
import sys

for _p in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)


def main():
    dry = "--dry-run" in sys.argv
    days = 10
    if "--days" in sys.argv:
        try:
            days = int(sys.argv[sys.argv.index("--days") + 1])
        except Exception:
            days = 10
    try:
        from app.services.wolf_volume_gate import enabled, run
        from app.services.wolf_eod import gate
    except Exception as e:
        print(f"[volume_gate] 导入失败: {e}")
        return 1
    if not enabled():
        print("[volume_gate] WOLF_VOLUME_GATE=0 → 跳过")
        return 0
    _g = gate()
    if _g == 1:
        return 0
    if _g == 2:
        return 2
    res = run(days=days, save=not dry)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
