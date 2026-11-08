#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_criteria_check.py — P2.5 判据匹配检查（只读，不算收益率）。

检查：
  - holding_days 是否为 20；
  - focus/trial 的 criteria_pass 是否 >= 4；
  - 打印每个主题的 criteria 明细。
"""
from __future__ import annotations
import argparse, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", default="20261023,20261021")
    a = ap.parse_args()
    errs = 0
    for date in [x.strip() for x in a.dates.split(",") if x.strip()]:
        path = os.path.join(ROOT, "data", "mainline", date, "theme.json")
        if not os.path.exists(path):
            print(f"{date}: MISSING"); errs += 1; continue
        j = json.load(open(path, encoding="utf-8"))
        print(f"\n=== {date} holding_days={j.get('holding_days')} mainline_confirmed={j.get('mainline_confirmed')} ===")
        print("rank | theme | verdict | criteria | failed | gate")
        print("---|---|---|---|---|---")
        for r in j.get("themes", []):
            if "error" in r:
                continue
            c = r.get("criteria") or {}
            failed = [k for k, v in c.items() if not v]
            print(f"{r.get('rank')} | {r['name']} | {r['verdict']} | "
                  f"{r.get('criteria_pass')}/{r.get('criteria_total')} | {','.join(failed) or '-'} | {r.get('gate','')}")
            if r["verdict"] in {"focus", "trial"} and r.get("criteria_pass", 0) < 4:
                print(f"  ❌ {r['id']}: focus/trial 但 criteria_pass<4")
                errs += 1
        if j.get("holding_days") != 20:
            print("  ❌ holding_days != 20"); errs += 1
    print(f"\n{'OK' if errs == 0 else 'FAIL'}: {errs} errors")


if __name__ == "__main__":
    main()
