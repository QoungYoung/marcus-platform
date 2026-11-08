#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_daily.py — P2 L2 细分日频评分（机械评分，不用 agent）。

输入：
  config/mainline/generated/universe_theme.yaml   # 27 主题的 source/leader/ladder
  config/mainline/generated/universe_track.yaml   # parent track
  data/mainline/{date}/mainline.json              # L0 gate
  data/mainline/{date}/track.json                 # L1 gate
输出：
  data/mainline/{date}/theme.json

评分：
  0.20*z(ex1) + 0.35*z(ex5) + 0.20*z(breadth5) + 0.15*z(leader_rs5) + 0.10*z(amount_ratio)
  - 主线未 confirmed：focus 降为 trial；
  - 父赛道 avoid：focus 降为 watch；
  - 卫星题材单独 focus/watch/avoid。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Set, Tuple

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "jobs"))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from mainline_common import (  # noqa: E402
    daily_snapshot, index_amount, index_close, last_trade_date, member_map,
    pct, trade_dates, write_json, zscores,
)
from core.tushare_relay import relay_query  # noqa: E402

THEME_CFG = os.path.join(ROOT, "config", "mainline", "generated", "universe_theme.yaml")


def load_theme_cfg():
    try:
        from app.services.theme_universe_store import load_theme_config
        x = load_theme_config()
        if x: return x
    except Exception:
        pass
    return load_yaml(THEME_CFG)
WEIGHT_CFG = os.path.join(ROOT, "config", "mainline", "theme_weights.yaml")
HOLD_CFG = os.path.join(ROOT, "config", "mainline", "holding_policy.yaml")
TRACK_CFG = os.path.join(ROOT, "config", "mainline", "generated", "universe_track.yaml")

_CSI_MEM: Dict[str, Set[str]] = {}
_DC_MEM: Dict[str, Set[str]] = {}
_DC_INDEX: Dict[str, Any] = {}
_CROWD: Dict[str, float] = {}
_SERIES_CACHE: Dict[Any, Any] = {}


def fetch_series(typ: str, code: str, start: str, end: str, retries: int = 3):
    key = (typ, code, start, end)
    if key in _SERIES_CACHE:
        v = _SERIES_CACHE[key]
        if isinstance(v, Exception):
            raise v
        return v
    last = None
    for i in range(retries):
        try:
            c = index_close(typ, code, start, end)
            a = index_amount(typ, code, start, end)
            _SERIES_CACHE[key] = (c, a)
            return c, a
        except Exception as e:
            last = e
            time.sleep(0.6 * (i + 1))
    _SERIES_CACHE[key] = last
    raise last


def load_crowding(date8: str) -> Dict[str, float]:
    if _CROWD:
        return _CROWD
    path = os.path.join(ROOT, "data", "fund_holdings", "aggregate.json")
    if not os.path.exists(path) or date8 < "20260630":
        return {}
    try:
        arr = json.load(open(path, encoding="utf-8"))
        for x in arr:
            if x.get("latest_end_date") and str(x["latest_end_date"]) <= date8:
                _CROWD[str(x.get("symbol"))] = float(x.get("sum_float_ratio") or 0.0)
    except Exception:
        _CROWD.clear()
    return _CROWD


def load_yaml(path: str):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def csi_members(code: str) -> Set[str]:
    if code in _CSI_MEM:
        return _CSI_MEM[code]
    out: Set[str] = set()
    for wd in ("20261023", "20260930", "20260630", "20260331"):
        try:
            w = relay_query("index_weight", index_code=code, trade_date=wd)
            if w is not None and len(w):
                out = set(w["con_code"].astype(str))
                break
        except Exception:
            continue
    _CSI_MEM[code] = out
    return out


def dc_members(code: str, date8: str) -> Set[str]:
    if code in _DC_MEM:
        return _DC_MEM[code]
    out: Set[str] = set()
    try:
        m = relay_query("dc_member", ts_code=code, trade_date=date8, limit=5000)
        if m is not None and len(m):
            out = set(m["con_code"].astype(str))
    except Exception:
        out = set()
    _DC_MEM[code] = out
    return out


def dc_breadth(date8: str) -> Dict[str, float]:
    if "map" in _DC_INDEX:
        return _DC_INDEX["map"]
    out: Dict[str, float] = {}
    try:
        d = relay_query("dc_index", trade_date=date8, limit=5000)
        if d is not None and len(d):
            for r in d.itertuples():
                up = float(getattr(r, "up_num", 0) or 0)
                down = float(getattr(r, "down_num", 0) or 0)
                if up + down > 0:
                    out[str(r.ts_code)] = up / (up + down)
    except Exception:
        out = {}
    _DC_INDEX["map"] = out
    return out


def source_members(src: Dict[str, Any], sw: Dict[str, Set[str]], date8: str) -> Set[str]:
    typ, code = str(src.get("type")), str(src.get("code"))
    if typ in {"sw_l2", "sw_l3"}:
        return sw.get(code, set())
    if typ == "csi":
        return csi_members(code)
    if typ == "eastmoney":
        return dc_members(code, date8)
    return set()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    a = ap.parse_args()
    date8 = last_trade_date(a.date)
    dates = trade_dates(a.date)
    d1 = dates[-2] if len(dates) >= 2 else dates[0]
    d5 = dates[-6] if len(dates) >= 6 else dates[0]
    d20 = dates[-21] if len(dates) >= 21 else dates[0]

    theme_cfg = load_theme_cfg()
    wcfg = load_yaml(WEIGHT_CFG).get("weights") or {}
    hold = load_yaml(HOLD_CFG)
    hold_days = int(hold.get("holding_days", 20))
    hr = hold.get("exit_rules") or {}
    crit_min = int(hr.get("criteria_pass_min", 4))
    W = {k: float(wcfg.get(k, d)) for k, d in {
        "ex1": 0.20, "ex5": 0.35, "ex20": 0.0, "breadth": 0.20,
        "leader_rs": 0.15, "amount_ratio": 0.10}.items()}
    try:
        from app.services.theme_universe_store import load_track_config
        track_cfg = load_track_config() or {}
    except Exception:
        track_cfg = {}
    if not track_cfg:
        track_cfg = load_yaml(TRACK_CFG)
    tracks = {str(t["id"]): t for t in (track_cfg.get("tracks") or []) + (track_cfg.get("satellite_tracks") or [])}
    themes = (theme_cfg.get("themes") or []) + (theme_cfg.get("satellite_themes") or [])

    ml = {}
    ml_path = os.path.join(ROOT, "data", "mainline", date8, "mainline.json")
    if os.path.exists(ml_path):
        try:
            ml = json.load(open(ml_path, encoding="utf-8"))
        except Exception:
            ml = {}
    _tradeable = bool(ml.get("tradeable", ml.get("confirmed")))
    tr = {}
    tr_path = os.path.join(ROOT, "data", "mainline", date8, "track.json")
    if os.path.exists(tr_path):
        try:
            tr = json.load(open(tr_path, encoding="utf-8"))
        except Exception:
            tr = {}
    track_verdict = {r["id"]: r.get("verdict") for r in (tr.get("tracks") or [])}
    bench = index_close("csi", "000001.SH", d20, date8)
    br1 = pct(float(bench.iloc[-1]), float(bench.iloc[-2])) if len(bench) >= 2 else 0.0
    br5 = pct(float(bench.iloc[-1]), float(bench.iloc[-6])) if len(bench) >= 6 else 0.0
    br20 = pct(float(bench.iloc[-1]), float(bench.iloc[0])) if len(bench) >= 2 else 0.0

    _snap_guard = daily_snapshot(date8)
    if _snap_guard is None or len(_snap_guard) == 0:
        print(f"[data_warning] {date8} 日线未生成（盘中/未收盘），请用上一交易日或 15:00 后重跑", file=sys.stderr)
        sys.exit(2)
    snap0 = _snap_guard.set_index("ts_code")["close"].to_dict()
    snap5 = daily_snapshot(d5).set_index("ts_code")["close"].to_dict()
    snap20 = daily_snapshot(d20).set_index("ts_code")["close"].to_dict()
    sw = member_map()
    dcb = dc_breadth(date8)
    crowd = load_crowding(date8)

    rows = []
    for t in themes:
        srcs = t.get("sources") or []
        rets1, rets5, rets20, amts = [], [], [], []
        breadth_vals = []
        theme_mem: Set[str] = set()
        source_errors = []
        used = []
        for s in srcs:
            typ, code = str(s.get("type")), str(s.get("code"))
            try:
                c, amt = fetch_series(typ, code, d20, date8)
                if len(c) < 2:
                    source_errors.append(f"{code}: empty series")
                    continue
                r1 = pct(float(c.iloc[-1]), float(c.iloc[-2])) if len(c) >= 2 else 0.0
                r5 = pct(float(c.iloc[-1]), float(c.iloc[-6])) if len(c) >= 6 else 0.0
                r20 = pct(float(c.iloc[-1]), float(c.iloc[0]))
                a5 = float(amt.tail(5).mean()) if len(amt) >= 5 else float(amt.mean() or 0)
                a20 = float(amt.tail(20).mean()) if len(amt) >= 20 else float(amt.mean() or 0)
                rets1.append(r1)
                rets5.append(r5)
                rets20.append(r20)
                amts.append(a5 / a20 if a20 > 0 else 1.0)
                used.append({"type": typ, "code": code, "name": s.get("name"),
                             "ret1": round(r1, 4), "ret5": round(r5, 4), "ret20": round(r20, 4)})
                mem = source_members(s, sw, date8)
                theme_mem |= set(mem)
                mem = [x for x in mem if x in snap0 and x in snap5]
                if mem:
                    breadth_vals.append(sum(1 for x in mem if snap0[x] > snap5[x]) / len(mem))
                elif typ == "eastmoney" and code in dcb:
                    breadth_vals.append(dcb[code])
            except Exception as e:
                source_errors.append(f"{code}: {type(e).__name__}: {str(e)[:80]}")
                continue
        if not rets5:
            rows.append({"id": t["id"], "name": t["name"], "error": "no source data",
                         "source_errors": source_errors,
                         "sleeve": t.get("sleeve"), "parent_track": t.get("parent_track")})
            continue
        r1 = sum(rets1) / len(rets1)
        r5 = sum(rets5) / len(rets5)
        r20 = sum(rets20) / len(rets20)
        amt_ratio = sum(amts) / len(amts)
        breadth = sum(breadth_vals) / len(breadth_vals) if breadth_vals else 0.5
        cvals = [crowd[c] for c in theme_mem if c in crowd]
        crowding_avg = round(sum(cvals) / len(cvals), 3) if cvals else 0.0
        crowding_max = round(max(cvals), 3) if cvals else 0.0
        crowding_cov = round(len(cvals) / max(1, len(theme_mem)), 2) if theme_mem else 0.0
        parent = t.get("parent_track")
        criteria = {
            "mainline_ok": _tradeable,
            "parent_ok": track_verdict.get(parent) != "avoid",
            "leader_ok": False,
            "breadth_ok": breadth >= 0.5,
            "amount_ok": amt_ratio >= 1.0,
            "trend_ok": r20 > -0.10,
            "crowding_ok": crowding_avg <= 2.0,
        }
        leader = (t.get("leader") or {}).get("code")
        lret5 = 0.0
        if leader and leader in snap0 and leader in snap5 and snap5[leader] > 0:
            lret5 = pct(snap0[leader], snap5[leader])
        leader_rs = lret5 - r5
        criteria["leader_ok"] = leader_rs > 0
        parent = t.get("parent_track")
        rows.append({
            "id": t["id"], "name": t["name"], "sleeve": t.get("sleeve"),
            "parent_track": parent,
            "parent_verdict": track_verdict.get(parent),
            "sources_used": used,
            "ret1": round(r1, 4), "ret5": round(r5, 4), "ret20": round(r20, 4),
            "excess1": round(r1 - br1, 4), "excess5": round(r5 - br5, 4), "excess20": round(r20 - br20, 4),
            "breadth": round(breadth, 4),
            "leader": t.get("leader") or {},
            "leader_ret5": round(lret5, 4), "leader_rs": round(leader_rs, 4),
            "amount_ratio": round(amt_ratio, 3),
            "criteria": criteria,
            "criteria_pass": int(sum(1 for v in criteria.values() if v)),
            "criteria_total": len(criteria),
            "holding_days": hold_days,
            "crowding_avg": crowding_avg, "crowding_max": crowding_max,
            "crowding_coverage": crowding_cov,
            "crowding_penalty": 0.0,
            "caps": t.get("caps") or {},
            "score": 0.0,
        })

    for sleeve in ("theme", "satellite"):
        group = [r for r in rows if r.get("sleeve") == sleeve and "error" not in r]
        if not group:
            continue
        z_e1 = zscores([r["excess1"] for r in group])
        z_e5 = zscores([r["excess5"] for r in group])
        z_e20 = zscores([r["excess20"] for r in group])
        z_br = zscores([r["breadth"] for r in group])
        z_lr = zscores([r["leader_rs"] for r in group])
        z_am = zscores([r["amount_ratio"] for r in group])
        z_cr = zscores([r.get("crowding_avg", 0.0) for r in group]) if crowd else [0.0] * len(group)
        for i, r in enumerate(group):
            pen = 0.15 * z_cr[i] if crowd and r.get("crowding_coverage", 0) > 0 else 0.0
            r["crowding_penalty"] = round(pen, 3)
            r["score"] = round(
                W["ex1"] * z_e1[i] + W["ex5"] * z_e5[i] + W["ex20"] * z_e20[i]
                + W["breadth"] * z_br[i] + W["leader_rs"] * z_lr[i]
                + W["amount_ratio"] * z_am[i] - pen, 3)
        group.sort(key=lambda x: -x["score"])
        for i, r in enumerate(group, 1):
            r["rank"] = i
            if sleeve == "satellite":
                r["verdict"] = "focus" if i == 1 and r["score"] > 0 else "watch" if i <= 2 else "avoid"
            else:
                r["verdict"] = "focus" if i <= 3 and r["score"] > 0 else "watch" if i <= 8 else "avoid"

    # gates
    unconfirmed = not _tradeable
    for r in rows:
        if r.get("sleeve") not in {"theme", "satellite"} or "error" in r:
            continue
        if r.get("verdict") in {"focus", "trial"} and r.get("criteria_pass", 0) < 4:
            r["verdict"] = "watch"
            r["gate"] = "criteria_pass"
        if r.get("verdict") == "focus" and r.get("parent_verdict") == "avoid":
            r["verdict"] = "watch"
            r["gate"] = "parent_track_avoid"
        if r.get("verdict") == "focus" and unconfirmed:
            r["verdict"] = "trial"
            r["gate"] = "mainline_unconfirmed"

    # 20 天持有：只输出判据有效性，hold/reduce/exit 交给现有仓位函数
    for r in rows:
        if "error" in r:
            continue
        cp = int(r.get("criteria_pass", 0))
        crit = r.get("criteria") or {}
        thesis_valid = bool(cp >= crit_min and crit.get("parent_ok") and crit.get("trend_ok"))
        r["thesis_valid"] = thesis_valid
        r["review_required"] = bool((not thesis_valid) or ml.get("regime") == "weak")
        r["failed_criteria"] = [k for k, v in crit.items() if not v]

    rows.sort(key=lambda x: (x.get("sleeve") != "theme", x.get("rank", 99)))
    focus = [r for r in rows if r.get("verdict") == "focus" and r.get("sleeve") == "theme"]
    trial = [r for r in rows if r.get("verdict") == "trial" and r.get("sleeve") == "theme"]
    watch = [r for r in rows if r.get("verdict") == "watch" and r.get("sleeve") == "theme"]
    avoid = [r for r in rows if r.get("verdict") == "avoid" and r.get("sleeve") == "theme"]
    sat_focus = [r for r in rows if r.get("verdict") == "focus" and r.get("sleeve") == "satellite"]

    out = {
        "as_of": date8, "freq": "daily", "benchmark": "000001.SH",
        "benchmark_ret1": round(br1, 4), "benchmark_ret5": round(br5, 4), "benchmark_ret20": round(br20, 4),
        "mainline_confirmed": bool(ml.get("confirmed")),
        "mainline_tradeable": _tradeable,
        "mainline_exists": bool(ml.get("mainline_exists", bool(ml.get("mainline")))),
        "mainline_active": bool(ml.get("mainline_active", False)),
        "mainline_status": ml.get("status"),
        "mainline_regime": ml.get("regime", "unknown"),
        "crowding_available": bool(crowd), "weights": W, "holding_days": hold_days,
        "holding_policy": hold,
        "focus": [{"id": r["id"], "name": r["name"], "score": r["score"], "parent": r["parent_track"]} for r in focus],
        "trial": [{"id": r["id"], "name": r["name"], "score": r["score"], "parent": r["parent_track"]} for r in trial],
        "watch": [{"id": r["id"], "name": r["name"], "score": r["score"], "parent": r["parent_track"]} for r in watch],
        "avoid": [{"id": r["id"], "name": r["name"], "score": r["score"], "parent": r["parent_track"]} for r in avoid],
        "satellite_focus": [{"id": r["id"], "name": r["name"], "score": r["score"]} for r in sat_focus],
        "themes": rows,
    }
    path = os.path.join(ROOT, "data", "mainline", date8, "theme.json")
    write_json(path, out)
    try:
        from app.services.theme_snapshot_store import save as save_snapshot
        save_snapshot(date8, out)
        print("[theme_daily] snapshot -> PG")
    except Exception as e:
        print("[theme_daily] PG snapshot fail:", str(e)[:120])
    print(f"as_of={date8} mainline_confirmed={out['mainline_confirmed']} regime={out['mainline_regime']} "
          f"focus={[r['name'] for r in focus]} trial={[r['name'] for r in trial]} "
          f"satellite={[r['name'] for r in sat_focus]}")
    for r in rows:
        if "error" in r:
            print(f"  {r['name']}: ERROR {r['error']}")
        elif r.get("sleeve") == "theme":
            print(f"  {r.get('rank'):>2} {r['name']:<12} score={r['score']:+.2f} "
                  f"ex1={r['excess1']:+.1%} ex5={r['excess5']:+.1%} br={r['breadth']:.0%} "
                  f"lrs={r['leader_rs']:+.1%} amt={r['amount_ratio']:.2f} "
                  f"parent={r.get('parent_verdict')} -> {r['verdict']}")
    print(f"written: {path}")


if __name__ == "__main__":
    main()
