#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_holding_check.py — 20 天持有的判据式 hold/exit 检查（只读）。"""
from __future__ import annotations
import argparse, json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", default="20261023,20261021,20260902,20260702,20260508")
    a = ap.parse_args()
    errs = 0
    for date in [x.strip() for x in a.dates.split(",") if x.strip()]:
        path = os.path.join(ROOT, "data", "mainline", date, "theme.json")
        if not os.path.exists(path):
            print(f"{date}: MISSING"); errs += 1; continue
        j = json.load(open(path, encoding="utf-8"))
        rows = [r for r in j.get("themes", []) if "error" not in r]
        err_rows = [r for r in j.get("themes", []) if "error" in r]
        counts = {}
        for r in rows:
            key = "valid" if r.get("thesis_valid") else "invalid"
            counts[key] = counts.get(key, 0) + 1
        bad = []
        for r in rows:
            cp = int(r.get("criteria_pass", 0))
            if r["verdict"] in {"focus", "trial"} and cp < 4:
                bad.append(f"{r['id']}: {r['verdict']} cp={cp}")
            if r["verdict"] in {"focus", "trial"} and not r.get("thesis_valid"):
                bad.append(f"{r['id']}: {r['verdict']} thesis_valid=false")
        print(f"{date} | holding={j.get('holding_days')} | focus={[x['id'] for x in j.get('focus',[])]} "
              f"trial={[x['id'] for x in j.get('trial',[])]} | thesis={counts} | errors={len(err_rows)}")
        if bad:
            errs += len(bad)
            for b in bad:
                print("  ❌", b)
        if j.get("holding_days") != 5:
            print("  ❌ holding_days != 5"); errs += 1
    print(f"\n{'OK' if errs == 0 else 'FAIL'}: {errs} errors")


if __name__ == "__main__":
    main()
