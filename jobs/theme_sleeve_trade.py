#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_sleeve_trade.py — 题材 sleeve 模拟盘下单。

读 latest theme_sleeve_selected.json → calc_position(sleeve=theme) → POST /trades(account=stock)。
默认 dry-run；--execute 才下单。同一日期/标的不重复下单。
"""
from __future__ import annotations
import argparse, json, os, sys, urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = (os.getenv("MARCUS_API_URL") or "http://backend:8000").rstrip("/")
if API.endswith("/api/v1"):
    API = API[:-7].rstrip("/")


def post(path, payload):
    req = urllib.request.Request(API + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def latest_selected():
    base = os.path.join(ROOT, "data", "mainline")
    ds = [x for x in os.listdir(base) if x.isdigit() and os.path.exists(os.path.join(base, x, "theme_sleeve_selected.json"))]
    return max(ds) if ds else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    d = latest_selected()
    if not d:
        print("no theme_sleeve_selected.json"); return 0
    p = os.path.join(ROOT, "data", "mainline", d, "theme_sleeve_selected.json")
    sel = json.load(open(p, encoding="utf-8"))
    state_p = os.path.join(ROOT, "data", "theme_sleeve_trade_state.json")
    try: state = json.load(open(state_p, encoding="utf-8"))
    except Exception: state = {}
    today = datetime.now().strftime("%Y%m%d")
    done = set(state.get(today) or [])
    results = []
    for x in sel.get("selected") or []:
        sym = x.get("symbol")
        if not sym or sym in done:
            continue
        payload = {"symbol": sym, "signal_strength": "medium", "chain_role": "mid", "tier": "probe",
                   "stance": "yellow", "intent": "new_base", "account_id": "stock",
                   "symbol_held": False, "t_universe": False, "rel_low": False, "mainline_dir": False,
                   "theme_id": x.get("theme_id"), "sleeve": "theme"}
        try:
            pos = post("/api/v1/indicator/calc-position", payload)
        except Exception as e:
            results.append({"symbol": sym, "ok": False, "error": "calc:" + str(e)[:120]}); continue
        q = pos.get("quantity") or {}
        shares = int(q.get("probe_shares") or 0)
        price = float(pos.get("current_price") or 0)
        rec = {"symbol": sym, "theme_id": x.get("theme_id"), "shares": shares, "price": price,
               "all_pass": pos.get("all_pass"), "warnings": (pos.get("warnings") or [])[:4]}
        if not a.execute:
            rec["ok"] = True; rec["dry_run"] = True; results.append(rec); continue
        if shares < 100 or price <= 0:
            rec["ok"] = False; rec["error"] = "no lot"; results.append(rec); continue
        try:
            order = post("/api/v1/trades", {"symbol": sym, "side": "buy", "price": price, "volume": shares,
                                             "reason": "theme_sleeve:%s" % x.get("theme_id"),
                                             "account": "stock", "intent": "probe"})
            rec["ok"] = bool(order.get("order_id")); rec["order"] = order
            if rec["ok"]:
                done.add(sym)
        except Exception as e:
            rec["ok"] = False; rec["error"] = "trade:" + str(e)[:160]
        results.append(rec)
    state[today] = sorted(done)
    json.dump(state, open(state_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    out = {"date": today, "selected_date": d, "execute": a.execute, "results": results}
    q = os.path.join(ROOT, "data", "mainline", d, "theme_sleeve_trade.json")
    json.dump(out, open(q, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
