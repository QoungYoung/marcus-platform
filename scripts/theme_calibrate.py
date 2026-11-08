#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_calibrate.py — P2.5 判例校准（只读）。

对给定日期，检查“判例主题”在 theme.json 里的排名/verdict，
输出 top3/top5 命中与 trial/focus 覆盖。
"""
from __future__ import annotations
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = {
    "20261023": {"humanoid_robot", "robot_actuator", "storage", "pcb", "semi_material"},
    "20261021": {"semi_equipment", "pcb", "optical_module", "semi_material"},
    "20260902": {"semi_material", "storage"},
    "20260702": {"storage", "semi_material"},
    "20260508": {"storage"},
}


def main() -> None:
    rows = []
    for date, expected in CASES.items():
        path = os.path.join(ROOT, "data", "mainline", date, "theme.json")
        if not os.path.exists(path):
            rows.append((date, "MISSING", [], [], [], []))
            continue
        j = json.load(open(path, encoding="utf-8"))
        themes = {r["id"]: r for r in j.get("themes", []) if "error" not in r}
        ranked = sorted([r for r in themes.values() if r.get("sleeve") == "theme"], key=lambda x: x.get("rank", 99))
        ranks = {r["id"]: (i + 1, r.get("verdict")) for i, r in enumerate(ranked)}
        details = [(e, ranks.get(e, (None, None))) for e in sorted(expected)]
        top3 = [e for e, (r, _) in details if r and r <= 3]
        top5 = [e for e, (r, _) in details if r and r <= 5]
        trial = [r["id"] for r in j.get("trial", [])]
        focus = [r["id"] for r in j.get("focus", [])]
        rows.append((date, f"{len(top3)}/{len(expected)}", f"{len(top5)}/{len(expected)}", details, trial, focus))
    print("date | top3 | top5 | expected(rank,verdict) | trial | focus")
    print("---|---|---|---|---|---")
    for date, t3, t5, details, trial, focus in rows:
        ds = ", ".join(f"{e}:{r}/{v}" for e, (r, v) in details)
        print(f"{date} | {t3} | {t5} | {ds} | {trial} | {focus}")
    print("\n说明：top3/top5 是“判例主题落在日频排名前 3/前 5”的比例；不是收益胜率。")


if __name__ == "__main__":
    main()
