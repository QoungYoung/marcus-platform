#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_coarse_screen.py — 题材粗筛（不交易，不单因子硬否）。

对最近区间的 theme.json：
  非主线 trial/watch
  flags: ret1>0 / breadth>=0.4 / leader>0 / amount_ratio>=1.1
  hits>=2 -> 粗筛候选
B4/筹码只作证据，不做硬门槛。
"""
from __future__ import annotations
import argparse, glob, json, os, sys
from datetime import datetime
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "core"), os.path.join(ROOT, "jobs"), os.path.join(ROOT, "backend")):
    if p not in sys.path: sys.path.insert(0, p)
from tushare_relay import relay_query


def load(p):
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20261027")
    ap.add_argument("--end", default="20261103")
    ap.add_argument("--max-per-day", type=int, default=5)
    a = ap.parse_args()
    mc = load(os.path.join(ROOT, "config", "mainline", "universe_mainline.yaml"))
    tc = load(os.path.join(ROOT, "config", "mainline", "generated", "universe_theme.yaml"))
    # 当前主线 tracks（粗筛只用它排除非主线；不判断主线 active）
    allowed = set()
    for m in mc.get("mainlines") or []:
        allowed |= set(m.get("tracks") or [])
    ts = {x["id"]: x for x in (tc.get("themes") or []) + (tc.get("satellite_themes") or [])}
    dirs = sorted(glob.glob(os.path.join(ROOT, "data", "mainline", "*")))
    dates = [os.path.basename(d) for d in dirs if os.path.basename(d).isdigit() and a.start <= os.path.basename(d) <= a.end
             and os.path.exists(os.path.join(d, "theme.json"))]
    out = []
    for d in dates:
        try:
            th = json.load(open(os.path.join(ROOT, "data", "mainline", d, "theme.json"), encoding="utf-8"))
        except Exception:
            continue
        try:
            df = relay_query("daily", trade_date=d, limit=6000)
            px = {str(r.get("ts_code")): float(r.get("pct_chg") or 0) for _, r in df.iterrows()} if df is not None else {}
        except Exception:
            px = {}
        full = {x.get("id"): x for x in (th.get("themes") or [])}
        rows = []
        for x in (th.get("focus") or []) + (th.get("trial") or []) + (th.get("watch") or []):
            tid = x.get("id"); t = ts.get(tid) or {}
            if t.get("parent_track") in allowed:
                continue
            f = full.get(tid) or {}
            lc = (t.get("leader") or {}).get("code")
            lp = px.get(lc)
            r1 = f.get("ret1"); br = f.get("breadth"); ar = f.get("amount_ratio")
            flags = {"ret1": (r1 or 0) > 0, "breadth": (br or 0) >= 0.4,
                     "leader": (lp or 0) > 0, "amount": (ar or 0) >= 1.1}
            hits = sum(1 for v in flags.values() if v)
            if hits < 2:
                continue
            rows.append({"theme_id": tid, "name": t.get("name") or f.get("name"),
                         "ret1": r1, "ret5": f.get("ret5"), "ret20": f.get("ret20"),
                         "breadth": br, "amount_ratio": ar, "leader": (t.get("leader") or {}),
                         "leader_pct": lp, "verdict": x.get("verdict") or f.get("verdict"),
                         "hits": hits, "flags": flags})
        rows.sort(key=lambda z: (-z["hits"], -(z.get("ret1") or -9)))
        out.extend([dict(r, date=d) for r in rows[:a.max_per_day]])
    p = os.path.join(ROOT, "data", "theme_coarse_screen.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("date | theme | ret1 | breadth | leader | ar | hits | verdict")
    for r in out:
        print(r["date"], "|", r["name"], "|", r["ret1"], "|", r["breadth"], "|",
              (r["leader"] or {}).get("name"), r["leader_pct"], "|", r["amount_ratio"], "|", r["hits"], "|", r["verdict"])
    print("written", p, "dates", len(dates), "rows", len(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
