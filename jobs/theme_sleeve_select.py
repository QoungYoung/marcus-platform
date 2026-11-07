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


def _buyable(sym: str) -> bool:
    """默认只买主板；创业板/科创板/北交所需要显式权限。"""
    if os.getenv("THEME_ALLOW_CHINEXT", "0") == "1":
        return True
    s = str(sym or "").upper()
    return s.startswith(("600", "601", "603", "605", "000", "001", "002", "003"))


def _theme_size(t: dict) -> float:
    """首日只做 probe，二波确认在 theme_sleeve_add 里加。"""
    if t.get("first_linkage"):
        return 0.5
    if t.get("tier") == "watch_strong":
        return 0.25
    return 0.0


def latest_date():
    try:
        from theme_sleeve_store import latest_date as pg_latest
        d = pg_latest("facts")
        if d: return d
    except Exception:
        pass
    base = os.path.join(ROOT, "data", "mainline")
    ds = [x for x in os.listdir(base) if x.isdigit() and os.path.exists(os.path.join(base, x, "theme_sleeve_facts.json"))]
    return max(ds) if ds else datetime.now().strftime("%Y%m%d")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    d = a.date or latest_date()
    p = os.path.join(ROOT, "data", "mainline", d, "theme_sleeve_facts.json")
    try:
        from theme_sleeve_store import load as load_sleeve
        data = load_sleeve(d, "facts")
    except Exception:
        data = None
    if not data:
        data = json.load(open(p, encoding="utf-8"))
    out = []
    for t in data.get("themes") or []:
        _ag = t.get("agent_state") or {}
        # tier 只作证据，不再硬过滤
        if (t.get("internal_linkage") or {}).get("state") != "linked":
            continue
        # first_linkage 只作证据，不再硬过滤
        if (t.get("ret1") or 0) <= 0:
            continue
        # B4 只作为 agent 证据；不再硬过滤
        _ag = t.get("agent_state") or {}
        # agent 只作证据/仓位，不再一票否决
        if t.get("overhang") is None:
            continue
        roles = t.get("roles") or {}
        sym = roles.get("leader")
        if not sym:
            continue
        # 账户权限不进入策略；交易层自行处理
        out.append({"date": d, "sleeve": "theme", "theme_id": t.get("theme_id"), "theme_name": t.get("name"),
                    "symbol": sym, "role": "leader", "tier": t.get("tier"),
                    "first_linkage": bool(t.get("first_linkage")),
                    "ret1": t.get("ret1"), "breadth": t.get("breadth"),
                    "b4": t.get("b4"), "overhang": t.get("overhang"),
                    "agent_action": _ag.get("action"), "size_multiplier": _theme_size(t),
                    "entry_type": "first_day_probe" if t.get("first_linkage") else "confirmation_add"})
    out.sort(key=lambda x: (not x["first_linkage"], x["tier"] != "probe_candidate", -(x.get("ret1") or 0)))
    # 不再按 2 个题材硬截断；agent 批准的都保留（symbol 去重）
    seen = set(); sel = []
    for x in out:
        if x["theme_id"] in seen or x["symbol"] in seen:
            continue
        seen.add(x["theme_id"]); seen.add(x["symbol"]); sel.append(x)
    res = {"as_of": d, "sleeve": "theme", "selected": sel, "candidates": len(out)}
    q = os.path.join(ROOT, "data", "mainline", d, "theme_sleeve_selected.json")
    json.dump(res, open(q, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    try:
        from theme_sleeve_store import save as save_sleeve
        save_sleeve(d, "selected", res)
    except Exception as e:
        print("[theme] PG selected save fail:", str(e)[:100])
    print("written", q, "selected", len(sel), "candidates", len(out))
    for x in sel:
        print(x["theme_id"], x["symbol"], x["tier"], "first", x["first_linkage"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
