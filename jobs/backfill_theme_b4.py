#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""backfill_theme_b4.py — 回填题材 B4 历史（最近 N 个交易日）。"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timedelta
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "jobs"), os.path.join(ROOT, "backend"), os.path.join(ROOT, "core")):
    if p not in sys.path:
        sys.path.insert(0, p)
from tushare_relay import relay_query  # noqa: E402
from stock_agent_facts import members_for  # noqa: E402


def load_yaml(p):
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _i(v):
    try:
        f = float(v)
        return 0 if f != f else int(f)
    except Exception:
        return 0


def stats(mem, lim):
    u = [lim[c] for c in mem if c in lim and str(lim[c].get("limit") or "").upper() == "U"]
    z = [lim[c] for c in mem if c in lim and str(lim[c].get("limit") or "").upper() == "Z"]
    d = [lim[c] for c in mem if c in lim and str(lim[c].get("limit") or "").upper() == "D"]
    times = [_i(x.get("limit_times")) for x in u]
    opens = max([_i(x.get("open_times")) for x in u] or [0])
    mx = max(times) if times else 0
    lad = {"1": sum(1 for x in times if x == 1), "2": sum(1 for x in times if x == 2), "3+": sum(1 for x in times if x >= 3)}
    tag = "plain"
    if u or z:
        if opens >= 2 or (z and len(z) / max(1, len(u) + len(z)) >= 0.4 and len(z) >= 2):
            tag = "open_heavy"
        elif len(u) >= 4 and mx <= 1:
            tag = "graduation"
        elif lad["1"] >= 1 and lad["2"] >= 1 and (lad["3+"] >= 1 or lad["2"] >= 2):
            tag = "complete"
    return {"u": len(u), "z": len(z), "d": len(d), "max_times": mx, "open_max": opens,
            "ladder": lad, "tag": tag}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    a = ap.parse_args()
    mcfg = load_yaml(os.path.join(ROOT, "config", "mainline", "universe_mainline.yaml"))
    try:
        from app.services.theme_universe_store import load_theme_config
        tcfg = load_theme_config() or {}
    except Exception:
        tcfg = {}
    if not tcfg:
        tcfg = load_yaml(os.path.join(ROOT, "config", "mainline", "generated", "universe_theme.yaml"))
    ml = json.load(open(os.path.join(ROOT, "data", "mainline", datetime.now().strftime("%Y%m%d"), "mainline.json"), encoding="utf-8")) if os.path.exists(os.path.join(ROOT, "data", "mainline", datetime.now().strftime("%Y%m%d"), "mainline.json")) else {}
    main_tracks = set()
    for m in mcfg.get("mainlines") or []:
        if m.get("id") == ml.get("mainline"):
            main_tracks = set(m.get("tracks") or [])
    themes = []
    for t in (tcfg.get("themes") or []) + (tcfg.get("satellite_themes") or []):
        if t.get("parent_track") in main_tracks:
            continue
        srcs = list(t.get("sources") or []) + list(((t.get("resolution") or {}).get("selected")) or [])
        t2 = dict(t); t2["sources"] = srcs
        try:
            mem = sorted(set(members_for(t2, datetime.now().strftime("%Y%m%d"))))
        except Exception:
            mem = []
        if mem:
            themes.append((t["id"], mem))
    hist_path = os.path.join(ROOT, "data", "theme_b4_history.json")
    try:
        hist = json.load(open(hist_path, encoding="utf-8"))
    except Exception:
        hist = {}
    # 最近 N 个工作日（按日历回退，limit_list_d 无数据的日期跳过）
    from mainline_common import trade_dates
    dates = trade_dates(datetime.now().strftime("%Y%m%d"))
    dates = dates[-a.days:]
    for d in dates:
        try:
            df = relay_query("limit_list_d", trade_date=d, limit=6000)
        except Exception:
            continue
        if df is None or not len(df):
            continue
        lim = {str(r.get("ts_code")): r for _, r in df.iterrows()}
        for tid, mem in themes:
            st = stats(mem, lim)
            old = [x for x in (hist.get(tid) or []) if str(x.get("date")) != str(d)]
            old.append(dict(st, date=str(d)))
            hist[tid] = sorted(old, key=lambda x: x["date"])[-120:]
        print("backfilled", d, flush=True)
    json.dump(hist, open(hist_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("written", hist_path, "themes", len(hist))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
