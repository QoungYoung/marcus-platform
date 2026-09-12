# -*- coding: utf-8 -*-
"""wolf_mainline_select.py — L1 方向层「选主线」（盘后，D1）。

用户目标：方向判定与狼大一致。设计经历史验收（`/app/data/eval_ms_robust.json`）：
**资格闸(gate) ∩ 近 5 日相对强度 top1 → 后 5 日超额 +0.622%（t=2.86, n=159）**，超过验收线（+0.41%, t>2.6）。
被实测否决的因子（边际加速/参与面扩散/位置分层/带动板块闸）只作诊断，不进分数。

用法: python jobs/wolf_mainline_select.py [--date YYYYMMDD] [--dry-run]
"""
import os
import sys

for _p in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)


def main():
    argv = sys.argv
    d8 = None
    if "--date" in argv:
        try:
            d8 = argv[argv.index("--date") + 1]
        except Exception:
            d8 = None
    try:
        from app.services.wolf_mainline_select import enabled, run
        from app.services.wolf_eod import gate
    except Exception as e:
        print(f"[mainline] 导入失败: {e}")
        return 1
    if not enabled():
        print("[mainline] WOLF_MAINLINE_SELECT=0 → 跳过")
        return 0
    _g = gate(d8)
    if _g == 1:
        return 0
    if _g == 2:
        return 2
    res = run(save=not ("--dry-run" in argv), date8=d8)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
