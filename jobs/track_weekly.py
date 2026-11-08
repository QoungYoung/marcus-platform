#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""track_weekly.py — P1 L1 赛道周频评分（机械评分，不用 agent）。

输入：config/mainline/generated/universe_track.yaml
输出：data/mainline/{as_of}/track.json

评分：
  excess20 35% + breadth 25% + leader_rs 20% + amount_ratio 20%
  - excess20 = 赛道 20 日收益 - 上证 20 日收益
  - breadth = 赛道成员 20 日上涨比例
  - leader_rs = 赛道 leader 20 日收益 - 赛道 20 日收益
  - amount_ratio = 近 5 日均额 / 近 20 日均额
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "jobs"))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from mainline_common import (  # noqa: E402
    clip, daily_snapshot, index_amount, index_close, last_trade_date, member_map,
    pct, trade_dates, write_json, zscores,
)

CFG = os.path.join(ROOT, "config", "mainline", "generated", "universe_track.yaml")


def load_yaml(path: str):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    a = ap.parse_args()
    date8 = last_trade_date(a.date)
    dates = trade_dates(a.date)
    d1 = dates[-2] if len(dates) >= 2 else dates[0]
    d5 = dates[-6] if len(dates) >= 6 else dates[0]
    d20 = dates[-21] if len(dates) >= 21 else dates[0]
    try:
        from app.services.theme_universe_store import load_track_config
        cfg = load_track_config() or {}
    except Exception:
        cfg = {}
    if not cfg:
        cfg = load_yaml(CFG)
    tracks = (cfg.get("tracks") or []) + (cfg.get("satellite_tracks") or [])

    _snap_guard = daily_snapshot(date8)
    if _snap_guard is None or len(_snap_guard) == 0:
        print(f"[data_warning] {date8} 日线未生成（盘中/未收盘），请用上一交易日或 15:00 后重跑", file=sys.stderr)
        sys.exit(2)
    snap0 = _snap_guard.set_index("ts_code")["close"].to_dict()
    snap5 = daily_snapshot(d5).set_index("ts_code")["close"].to_dict()
    snap20 = daily_snapshot(d20).set_index("ts_code")["close"].to_dict()
    members = member_map()
    bclose = index_close("csi", "000001.SH", d20, date8)
    bret = pct(float(bclose.iloc[-1]), float(bclose.iloc[0])) if len(bclose) >= 2 else 0.0
    bret1 = pct(float(bclose.iloc[-1]), float(bclose.iloc[-2])) if len(bclose) >= 2 else 0.0
    bret5 = pct(float(bclose.iloc[-1]), float(bclose.iloc[-6])) if len(bclose) >= 6 else 0.0

    rows = []
    for t in tracks:
        code = t.get("sw_l2")
        if not code:
            continue
        try:
            close = index_close("sw_l2", code, d20, date8)
            amount = index_amount("sw_l2", code, d20, date8)
        except Exception as e:
            rows.append({"id": t["id"], "name": t.get("name"), "sw_l2": code, "error": str(e)[:120]})
            continue
        if len(close) < 2:
            rows.append({"id": t["id"], "name": t.get("name"), "sw_l2": code, "error": "no sw_daily"})
            continue
        ret1 = pct(float(close.iloc[-1]), float(close.iloc[-2])) if len(close) >= 2 else 0.0
        ret5 = pct(float(close.iloc[-1]), float(close.iloc[-6])) if len(close) >= 6 else 0.0
        ret20 = pct(float(close.iloc[-1]), float(close.iloc[0]))
        amt5 = float(amount.tail(5).mean()) if len(amount) >= 5 else float(amount.mean() or 0)
        amt20 = float(amount.tail(20).mean()) if len(amount) >= 20 else float(amount.mean() or 0)
        amt_ratio = amt5 / amt20 if amt20 > 0 else 1.0
        mem = members.get(str(code), set())
        mem5 = [c for c in mem if c in snap0 and c in snap5]
        mem20 = [c for c in mem if c in snap0 and c in snap20]
        breadth = (sum(1 for c in mem5 if snap0[c] > snap5[c]) / len(mem5)) if mem5 else 0.0
        breadth20 = (sum(1 for c in mem20 if snap0[c] > snap20[c]) / len(mem20)) if mem20 else 0.0
        leader = (t.get("leader") or {}).get("code")
        lret = 0.0
        lret5 = 0.0
        if leader and leader in snap0 and leader in snap5 and snap5[leader] > 0:
            lret5 = pct(snap0[leader], snap5[leader])
        if leader and leader in snap0 and leader in snap20 and snap20[leader] > 0:
            lret = pct(snap0[leader], snap20[leader])
        leader_rs = lret5 - ret5
        rows.append({
            "id": t["id"], "name": t.get("name"), "sw_l2": code,
            "sleeve": "satellite" if str(t.get("status")) == "satellite" else "track",
            "ret1": round(ret1, 4), "ret5": round(ret5, 4), "ret20": round(ret20, 4),
            "excess1": round(ret1 - bret1, 4),
            "excess5": round(ret5 - bret5, 4), "excess20": round(ret20 - bret, 4),
            "breadth": round(breadth, 4), "breadth20": round(breadth20, 4),
            "members": len(mem),
            "leader": t.get("leader") or {},
            "leader_ret5": round(lret5, 4), "leader_ret20": round(lret, 4),
            "leader_rs": round(leader_rs, 4),
            "amount_ratio": round(amt_ratio, 3),
            "caps": t.get("caps") or {},
            "score": 0.0,
        })

    # 主板赛道与卫星分开打分
    for sleeve in ("track", "satellite"):
        group = [r for r in rows if r.get("sleeve") == sleeve and "error" not in r]
        if not group:
            continue
        z_e1 = zscores([r["excess1"] for r in group])
        z_ex = zscores([r["excess5"] for r in group])
        z_br = zscores([r["breadth"] for r in group])
        z_lr = zscores([r["leader_rs"] for r in group])
        z_am = zscores([r["amount_ratio"] for r in group])
        for i, r in enumerate(group):
            r["score"] = round(
                0.20 * z_e1[i] + 0.30 * z_ex[i] + 0.20 * z_br[i]
                + 0.15 * z_lr[i] + 0.15 * z_am[i], 3)
        group.sort(key=lambda x: -x["score"])
        for i, r in enumerate(group, 1):
            r["rank"] = i
            if sleeve == "satellite":
                r["verdict"] = "focus" if i == 1 and r["score"] > 0 else "watch" if i <= 2 else "avoid"
            else:
                r["verdict"] = "focus" if i <= 3 and r["score"] > 0 else "watch" if i <= 5 else "avoid"

    rows.sort(key=lambda x: (x.get("sleeve") != "track", x.get("rank", 99)))
    ml_path = os.path.join(ROOT, "data", "mainline", date8, "mainline.json")
    ml = {}
    if os.path.exists(ml_path):
        try:
            import json
            ml = json.load(open(ml_path, encoding="utf-8"))
        except Exception:
            ml = {}
    # 主线未确认（pullback/weak）时，track focus 降为 trial，不新开
    _tradeable = bool(ml.get("tradeable", ml.get("confirmed")))
    if not _tradeable:
        for r in rows:
            if r.get("verdict") == "focus" and r.get("sleeve") == "track":
                r["verdict"] = "trial"
    focus = [r for r in rows if r.get("verdict") == "focus" and r.get("sleeve") == "track"]
    trial = [r for r in rows if r.get("verdict") == "trial" and r.get("sleeve") == "track"]
    watch = [r for r in rows if r.get("verdict") == "watch" and r.get("sleeve") == "track"]
    avoid = [r for r in rows if r.get("verdict") == "avoid" and r.get("sleeve") == "track"]
    satellite_focus = [r for r in rows if r.get("verdict") == "focus" and r.get("sleeve") == "satellite"]
    out = {
        "as_of": date8, "freq": "weekly", "benchmark": "000001.SH",
        "mainline_confirmed": bool(ml.get("confirmed")),
        "mainline_tradeable": _tradeable,
        "mainline_exists": bool(ml.get("mainline_exists", bool(ml.get("mainline")))),
        "mainline_active": bool(ml.get("mainline_active", False)),
        "mainline_status": ml.get("status"),
        "mainline_regime": ml.get("regime", "unknown"),
        "mainline_sub": ml.get("sub_mainline"),
        "benchmark_ret20": round(bret, 4),
        "focus": [{"id": r["id"], "name": r["name"], "score": r["score"], "verdict": r["verdict"]} for r in focus],
        "trial": [{"id": r["id"], "name": r["name"], "score": r["score"], "verdict": r["verdict"]} for r in trial],
        "watch": [{"id": r["id"], "name": r["name"], "score": r["score"], "verdict": r["verdict"]} for r in watch],
        "avoid": [{"id": r["id"], "name": r["name"], "score": r["score"], "verdict": r["verdict"]} for r in avoid],
        "tracks": rows,
        "project_caps": {r["id"]: r.get("caps", {}) for r in rows if r.get("caps")},
    }
    path = os.path.join(ROOT, "data", "mainline", date8, "track.json")
    write_json(path, out)
    print(f"as_of={date8} mainline_confirmed={out['mainline_confirmed']} regime={out['mainline_regime']} "
          f"b1={bret1:+.2%} b5={bret5:+.2%} b20={bret:+.2%} "
          f"focus={[r['name'] for r in focus]} trial={[r['name'] for r in trial]} "
          f"satellite={[r['name'] for r in satellite_focus]}")
    for r in rows:
        if "error" in r:
            print(f"  {r['name']}: ERROR {r['error']}")
        elif r.get("sleeve") == "track":
            print(f"  {r.get('rank'):>2} {r['name']:<12} score={r['score']:+.2f} "
                  f"ex1={r['excess1']:+.1%} ex5={r['excess5']:+.1%} ex20={r['excess20']:+.1%} br5={r['breadth']:.0%} lrs={r['leader_rs']:+.1%} "
                  f"amt={r['amount_ratio']:.2f} -> {r['verdict']}")
    print(f"written: {path}")


if __name__ == "__main__":
    main()
