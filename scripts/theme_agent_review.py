#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_agent_review.py — P2.5 主题日频 agent 复核（校验/落地）。

- 读 data/mainline/{date}/theme.json
- 读 data/mainline/{date}/theme_agent_review.json
- 校验：theme_id 存在、verdict 合法、mainline 未确认时禁止 focus、confidence 0-1
- --apply 写 theme_agent_applied.json；--promote 更新 theme.json（备份）
"""
from __future__ import annotations
import argparse, json, os, shutil, sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED = {"focus", "trial", "watch", "avoid"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20261023")
    ap.add_argument("--review", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--promote", action="store_true")
    a = ap.parse_args()
    base = os.path.join(ROOT, "data", "mainline", a.date)
    theme_path = os.path.join(base, "theme.json")
    review_path = a.review or os.path.join(base, "theme_agent_review.json")
    theme = json.load(open(theme_path, encoding="utf-8"))
    review = json.load(open(review_path, encoding="utf-8"))
    by = {r["id"]: r for r in theme.get("themes", []) if "error" not in r}
    confirmed = bool(theme.get("mainline_confirmed"))
    errs, warns, rows = [], [], []
    for r in review:
        tid = str(r.get("theme_id"))
        if tid not in by:
            errs.append(f"{tid}: 不在 theme.json")
            continue
        v = str(r.get("verdict"))
        if v not in ALLOWED:
            errs.append(f"{tid}: verdict 非法 {v}")
        if v == "focus" and not confirmed:
            errs.append(f"{tid}: mainline 未确认，禁止 focus")
        try:
            c = float(r.get("confidence"))
            if not (0 <= c <= 1):
                errs.append(f"{tid}: confidence 越界")
        except Exception:
            errs.append(f"{tid}: confidence 非数值")
        if not isinstance(r.get("reasons"), list):
            errs.append(f"{tid}: reasons 非数组")
        if r.get("needs_human"):
            warns.append(f"{tid}: needs_human=true（自动确认，仅审计）")
        rows.append((tid, by[tid]["verdict"], v, r.get("confidence")))
    print("theme | mechanical | agent | conf | validate")
    print("---|---|---|---:|---")
    for tid, old, new, conf in rows:
        bad = any(x.startswith(tid + ":") for x in errs)
        print(f"{tid} | {old} | {new} | {conf} | {'FAIL' if bad else 'OK'}")
    if warns:
        print("\nWARNINGS:"); [print("  ⚠️", w) for w in warns]
    if errs:
        print("\nERRORS:"); [print("  ❌", e) for e in errs]
    print(f"\n{'OK' if not errs else 'FAIL'}: {len(errs)} errors, {len(warns)} warnings, {len(rows)} themes")
    out = {"date": a.date, "review": review, "errors": errs, "warnings": warns}
    json.dump(out, open(os.path.join(base, "theme_agent_validated.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if a.apply and not errs:
        applied = json.loads(json.dumps(theme))
        ab = {r["id"]: r for r in applied.get("themes", [])}
        for r in review:
            if r["theme_id"] in ab:
                ab[r["theme_id"]]["verdict"] = r["verdict"]
                ab[r["theme_id"]]["agent_reviewed"] = True
                ab[r["theme_id"]]["agent_confidence"] = r.get("confidence")
        json.dump(applied, open(os.path.join(base, "theme_agent_applied.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("written: theme_agent_applied.json")
        if a.promote:
            bak = os.path.join(base, "theme.before_agent.json")
            if not os.path.exists(bak):
                shutil.copy(theme_path, bak)
            json.dump(applied, open(theme_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print("promoted: theme.json (backup theme.before_agent.json)")
    if errs:
        sys.exit(1)


if __name__ == "__main__":
    main()
