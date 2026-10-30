#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_sleeve_select.py — 题材 sleeve 选股（确定性，不交易）。

读 theme_sleeve_facts.json：
  tier in probe_candidate/watch_strong
  internal.state == linked
  b4.tag not in failed/graduation
  first_linkage 优先
每题材只选 leader，最多 2 个题材。
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def latest_date():
    base = os.path.join(ROOT, "data", "mainline")
    ds = [x for x in os.listdir(base) if x.isdigit() and os.path.exists(os.path.join(base, x, "theme_sleeve_facts.json"))]
    return max(ds) if ds else datetime.now().strftime("%Y%m%d")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    d = a.date or latest_date()
    p = os.path.join(ROOT, "data", "mainline", d, "theme_sleeve_facts.json")
    data = json.load(open(p, encoding="utf-8"))
    out = []
    for t in data.get("themes") or []:
        if t.get("tier") not in ("probe_candidate", "watch_strong"):
            continue
        if (t.get("internal_linkage") or {}).get("state") != "linked":
            continue
        if (t.get("b4") or {}).get("tag") in ("failed", "graduation"):
            continue
        roles = t.get("roles") or {}
        sym = roles.get("leader")
        if not sym:
            continue
        out.append({"date": d, "sleeve": "theme", "theme_id": t.get("theme_id"), "theme_name": t.get("name"),
                    "symbol": sym, "role": "leader", "tier": t.get("tier"),
                    "first_linkage": bool(t.get("first_linkage")),
                    "ret1": t.get("ret1"), "breadth": t.get("breadth"),
                    "b4": t.get("b4"), "overhang": t.get("overhang")})
    out.sort(key=lambda x: (not x["first_linkage"], x["tier"] != "probe_candidate", -(x.get("ret1") or 0)))
    # 只保留 2 个题材，且 symbol 不重复
    seen = set(); sel = []
    for x in out:
        if x["theme_id"] in seen or x["symbol"] in seen:
            continue
        seen.add(x["theme_id"]); seen.add(x["symbol"]); sel.append(x)
        if len(sel) >= 2:
            break
    res = {"as_of": d, "sleeve": "theme", "selected": sel, "candidates": len(out)}
    q = os.path.join(ROOT, "data", "mainline", d, "theme_sleeve_selected.json")
    json.dump(res, open(q, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("written", q, "selected", len(sel), "candidates", len(out))
    for x in sel:
        print(x["theme_id"], x["symbol"], x["tier"], "first", x["first_linkage"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
