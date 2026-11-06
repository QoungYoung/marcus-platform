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


def b_done_today(today: str) -> set:
    """B 通道今天已成功买入的标的（防止 A 再买一次）。"""
    try:
        p = os.path.join(ROOT, "data", "theme_b_exec_state.json")
        st = json.load(open(p, encoding="utf-8"))
        out = set()
        for sym, rec in (st.get(today) or {}).items():
            gw = (rec or {}).get("gateway") or {}
            if gw.get("status") == "success":
                out.add(str(sym).upper())
        return out
    except Exception:
        return set()


def latest_selected():
    try:
        from theme_sleeve_store import latest_date
        d = latest_date("selected")
        if d: return d
    except Exception:
        pass
    base = os.path.join(ROOT, "data", "mainline")
    ds = [x for x in os.listdir(base) if x.isdigit() and os.path.exists(os.path.join(base, x, "theme_sleeve_selected.json"))]
    return max(ds) if ds else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--date", default="", help="历史回放日期 YYYYMMDD；默认取 latest")
    a = ap.parse_args()
    d = a.date or latest_selected()
    if not d:
        print("no theme_sleeve_selected.json"); return 0
    p = os.path.join(ROOT, "data", "mainline", d, "theme_sleeve_selected.json")
    try:
        from theme_sleeve_store import load as load_sleeve
        sel = load_sleeve(d, "selected")
    except Exception:
        sel = None
    if not sel:
        sel = json.load(open(p, encoding="utf-8"))
    state_p = os.path.join(ROOT, "data", "theme_sleeve_trade_state.json")
    try: state = json.load(open(state_p, encoding="utf-8"))
    except Exception: state = {}
    today = d
    done = set(state.get(today) or [])
    b_done = b_done_today(today)
    results = []
    for x in sel.get("selected") or []:
        sym = x.get("symbol")
        if not sym or sym in done:
            continue
        if str(sym).upper() in b_done:
            results.append({"symbol": sym, "theme_id": x.get("theme_id"), "ok": False,
                            "skipped": "B_already_executed_today"})
            continue
        payload = {"symbol": sym, "signal_strength": "medium", "chain_role": "mid", "tier": "probe",
                   "stance": "yellow", "intent": "new_base", "account_id": "stock",
                   "symbol_held": False, "t_universe": False, "rel_low": False, "mainline_dir": False,
                   "theme_id": x.get("theme_id"), "sleeve": "theme"}
        # 共享买入前门：P2 + B4/B5 + B8（选股后、calc_position 前）
        try:
            _g = post("/api/v1/indicator/pre-trade-gate",
                      {"symbol": sym, "intent": "new_base", "source": "theme_sleeve_trade"})
        except Exception as _ge:
            _g = {"allow": True, "multiplier": 1.0,
                  "reasons": ["pre_trade_gate 调用失败(fail-open): " + str(_ge)[:120]]}
        if not _g.get("allow", True):
            results.append({"symbol": sym, "theme_id": x.get("theme_id"), "ok": False,
                            "blocked": True, "reasons": _g.get("reasons") or [], "gate": _g})
            continue
        _mult = float(_g.get("multiplier") or 1.0)
        if _mult < 1.0:
            payload["signal_strength"] = "low"
            payload["tier"] = "probe"
        try:
            pos = post("/api/v1/indicator/calc-position", payload)
        except Exception as e:
            results.append({"symbol": sym, "ok": False, "error": "calc:" + str(e)[:120]}); continue
        q = pos.get("quantity") or {}
        shares = int(q.get("probe_shares") or 0)
        _sz = float(x.get("size_multiplier") or 1.0)  # 已由题材策略算好，agent 不参与
        if _sz < 1.0 and shares >= 100:
            shares = max(100, int(shares * _sz / 100) * 100)
        price = float(pos.get("current_price") or 0)
        rec = {"symbol": sym, "theme_id": x.get("theme_id"), "shares": shares, "price": price,
               "all_pass": pos.get("all_pass"), "warnings": (pos.get("warnings") or [])[:4],
               "gate": {"allow": _g.get("allow"), "multiplier": _mult, "reasons": _g.get("reasons") or []}}
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
                try:
                    pp = os.path.join(ROOT, "data", "theme_sleeve_positions.json")
                    try: pdata = json.load(open(pp, encoding="utf-8"))
                    except Exception: pdata = {}
                    pdata[sym] = {"symbol": sym, "theme_id": x.get("theme_id"), "volume": shares,
                                  "cost": price, "entry_date": today}
                    json.dump(pdata, open(pp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                except Exception as e:
                    print("[theme] positions save fail:", str(e)[:100], flush=True)
        except Exception as e:
            rec["ok"] = False; rec["error"] = "trade:" + str(e)[:160]
        results.append(rec)
    state[today] = sorted(done)
    json.dump(state, open(state_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    out = {"date": today, "selected_date": d, "execute": a.execute, "results": results}
    q = os.path.join(ROOT, "data", "mainline", d, "theme_sleeve_trade.json")
    json.dump(out, open(q, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    try:
        from theme_sleeve_store import save
        save(d, "trade", out)
    except Exception as e:
        print("[theme] PG trade save fail:", str(e)[:120], flush=True)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
