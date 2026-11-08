#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""stock_select.py — P3 个股事实清单（不做单因子排序，不替用户选股）。

修正点（2026-10-27）：
  - 不再按 5 日涨幅排序；
  - 不再把 theme YAML 的 leader/ladder 当成“核心/龙头”；
  - 只输出事实：1d/5d/20d 涨幅、20 日高低距离、成交额、换手、市值排名、
    拥挤度、主题 verdict/thesis/caps；
  - 分类（核心/风向标/龙头/龙2/杂毛）留给 agent/用户，不写死单因子。

用法：
  python jobs/stock_select.py --date 20261023 --max 12
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "jobs"))

from mainline_common import last_trade_date, pct, trade_dates, write_json  # noqa: E402
from core.tushare_relay import relay_query  # noqa: E402

THEME = os.path.join(ROOT, "config", "mainline", "generated", "universe_theme.yaml")


def load_theme_cfg():
    try:
        from app.services.theme_universe_store import load_theme_config
        x = load_theme_config()
        if x: return x
    except Exception:
        pass
    return load_yaml(THEME)


def load_yaml(path: str):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_crowd() -> dict:
    p = os.path.join(ROOT, "data", "fund_holdings", "aggregate.json")
    if not os.path.exists(p):
        return {}
    try:
        arr = json.load(open(p, encoding="utf-8"))
        return {str(x.get("symbol")): float(x.get("sum_float_ratio") or 0) for x in arr}
    except Exception:
        return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--max", type=int, default=12)
    a = ap.parse_args()
    date8 = last_trade_date(a.date)
    ds = trade_dates(a.date)
    d1 = ds[-2] if len(ds) >= 2 else ds[0]
    d5 = ds[-6] if len(ds) >= 6 else ds[0]
    d20 = ds[-21] if len(ds) >= 21 else ds[0]
    theme_json = json.load(open(os.path.join(ROOT, "data", "mainline", date8, "theme.json"), encoding="utf-8"))
    rows = {r["id"]: r for r in theme_json.get("themes", []) if "error" not in r}
    cfg = load_theme_cfg()
    tcfg = {t["id"]: t for t in (cfg.get("themes") or []) + (cfg.get("satellite_themes") or [])}
    active = [x["id"] for x in (theme_json.get("focus") or []) + (theme_json.get("trial") or [])]
    if not active:
        print("没有 focus/trial 主题，P3 不产生候选")
        write_json(os.path.join(ROOT, "data", "mainline", date8, "stock_candidates.json"),
                   {"as_of": date8, "holding_days": 5, "selection_mode": "facts_only",
                    "themes": [], "candidates": []})
        return
    _snap_guard = daily_snapshot(date8)
    if _snap_guard is None or len(_snap_guard) == 0:
        print(f"[data_warning] {date8} 日线未生成（盘中/未收盘），请用上一交易日或 15:00 后重跑", file=sys.stderr)
        sys.exit(2)
    try:
        db = relay_query("daily_basic", trade_date=date8, limit=6000)
        dbmap = {str(r.ts_code): r for r in db.itertuples()}
    except Exception:
        dbmap = {}
    crowd = load_crowd()
    out = []
    seen = {}
    for tid in active:
        t = tcfg.get(tid) or {}
        tr = rows.get(tid) or {}
        seed = [("leader", t.get("leader"))] + [("ladder", y) for y in (t.get("ladder") or [])]
        for role, x in seed:
            if not x or not x.get("code"):
                continue
            code = str(x["code"])
            name = x.get("name") or code
            if code in seen:
                seen[code]["themes"].append(tid)
                if role not in seen[code]["seed_roles"]:
                    seen[code]["seed_roles"].append(role)
                continue
            if "ST" in name.upper() or "退" in name:
                continue
            try:
                d = relay_query("daily", ts_code=code, start_date=d20, end_date=date8)
                if d is None or len(d) < 2:
                    continue
                d = d.sort_values("trade_date")
                close = [float(v) for v in d["close"]]
                high = [float(v) for v in d["high"]]
                low = [float(v) for v in d["low"]]
                amt = [float(v or 0) for v in d["amount"]]
            except Exception:
                continue
            ret1 = pct(close[-1], close[-2]) if len(close) >= 2 else 0.0
            ret5 = pct(close[-1], close[-6]) if len(close) >= 6 else 0.0
            ret20 = pct(close[-1], close[0]) if len(close) >= 2 else 0.0
            hi = max(high) if high else close[-1]
            lo = min(low) if low else close[-1]
            r = dbmap.get(code)
            circ_mv = float(getattr(r, "circ_mv", 0) or 0) if r else 0.0
            turnover = float(getattr(r, "turnover_rate", 0) or 0) if r else 0.0
            item = {
                "symbol": code, "name": name,
                "themes": [tid], "theme_names": [t.get("name")],
                "seed_roles": [role],
                "selection_mode": "facts_only",
                "close": round(close[-1], 3),
                "ret1": round(ret1, 4), "ret5": round(ret5, 4), "ret20": round(ret20, 4),
                "dist_high20": round(pct(close[-1], hi), 4),
                "dist_low20": round(pct(close[-1], lo), 4),
                "amount5_yi": round(sum(amt[-5:]) / max(1, len(amt[-5:])) / 1e5, 2),
                "turnover": round(turnover, 2),
                "circ_mv_yi": round(circ_mv / 1e4, 1),
                "crowding_float": round(crowd.get(code, 0.0), 2),
                "theme_verdict": tr.get("verdict"),
                "thesis_valid": tr.get("thesis_valid"),
                "review_required": tr.get("review_required"),
                "caps": tr.get("caps") or t.get("caps") or {},
                "holding_days": 5,
            }
            seen[code] = item
            out.append(item)
    # 主题内市值排名（事实，不做打分）
    for tid in active:
        group = [x for x in out if tid in x["themes"]]
        ranked = sorted(group, key=lambda x: -x["circ_mv_yi"])
        for i, x in enumerate(ranked, 1):
            x.setdefault("mv_rank", {})[tid] = i
    out = out[: a.max]
    res = {"as_of": date8, "holding_days": 5, "selection_mode": "facts_only",
           "themes": active, "candidates": out,
           "notes": ["不按 5 日涨幅排序", "leader/ladder 仅为 seed_role，不是核心/龙头结论",
                     "分类需按核心/风向标/龙头/龙2/杂毛人工或 agent 判定"]}
    path = os.path.join(ROOT, "data", "mainline", date8, "stock_candidates.json")
    write_json(path, res)
    print(f"as_of={date8} themes={active} candidates={len(out)} mode=facts_only")
    print("theme | seed | symbol | name | ret1 | ret5 | ret20 | mv_yi | mv_rank | dist_high | amount5_yi | turnover | crowd | verdict")
    print("---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---")
    for x in out:
        tid = x["themes"][0]
        print(f"{x['theme_names'][0]} | {'/'.join(x['seed_roles'])} | {x['symbol']} | {x['name']} | "
              f"{x['ret1']:+.1%} | {x['ret5']:+.1%} | {x['ret20']:+.1%} | {x['circ_mv_yi']} | "
              f"{x.get('mv_rank',{}).get(tid,'-')} | {x['dist_high20']:+.1%} | {x['amount5_yi']} | "
              f"{x['turnover']} | {x['crowding_float']} | {x['theme_verdict']}")
    print(f"written: {path}")


if __name__ == "__main__":
    main()
