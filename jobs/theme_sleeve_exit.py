#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_sleeve_exit.py — 题材卖出：v3 点位 agent + 题材规则。

默认 dry-run；--execute 才通过 /trades 卖出（account=stock）。
规则优先级：
  1. -3% 硬止损
  2. 持有 >=3 个交易日
  3. +7% 卖半
  4. v3 agent break_trigger 触发 -> 清
  5. v3 agent rebound_zone 命中且浮盈 -> 卖半
"""
from __future__ import annotations
import argparse, json, os, sys, urllib.request
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "backend"), os.path.join(ROOT, "core"), os.path.join(ROOT, "jobs")):
    if p not in sys.path: sys.path.insert(0, p)
API = (os.getenv("MARCUS_API_URL") or "http://backend:8000").rstrip("/")
if API.endswith("/api/v1"): API = API[:-7]


def post(path, payload):
    req = urllib.request.Request(API + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def load_positions():
    p = os.path.join(ROOT, "data", "theme_sleeve_positions.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    # 从修正后的 trade 记录恢复
    tp = os.path.join(ROOT, "data", "mainline", "20261030", "theme_sleeve_trade.json")
    out = {}
    try:
        d = json.load(open(tp, encoding="utf-8"))
        for r in d.get("results") or []:
            if r.get("ok") and r.get("shares"):
                sym = r["symbol"]
                out[sym] = {"symbol": sym, "theme_id": r.get("theme_id"), "volume": int(r["shares"]),
                            "cost": float(r.get("price") or 0), "entry_date": d.get("date")}
    except Exception:
        pass
    return out


def quote(sym):
    from app.services.t_data_sources import fetch_quote_one
    q = fetch_quote_one(sym) or {}
    px = float(q.get("current") or 0)
    return q, px


def ts_code(sym):
    s = sym.upper().replace(".SH", "").replace(".SZ", "")
    if s.startswith(("SH", "SZ")): s = s[2:]
    return s + (".SH" if s.startswith("6") else ".SZ")


def v3_levels(sym, q, px):
    try:
        from app.services.wolf_v3_production import build_snapshot, ask_agent, validate_decision
        from tushare_relay import relay_query
        code = ts_code(sym)
        end = datetime.now().strftime("%Y%m%d"); start = (datetime.now() - timedelta(days=120)).strftime("%Y%m%d")
        df = relay_query("daily", ts_code=code, start_date=start, end_date=end, limit=120)
        bars = [] if df is None else df.to_dict("records")
        snap = build_snapshot(code, q, bars)
        dec = ask_agent(snap, timeout=45)
        if not dec.get("ok"):
            return {"ok": False, "error": dec.get("reason") or dec.get("error")}
        v = validate_decision(dec.get("decision") or {}, snap)
        return {"ok": bool(v.get("ok")), "decision": v.get("decision") or dec.get("decision"), "snapshot": snap}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    pos = load_positions()
    rows = []
    for sym, p in pos.items():
        q, px = quote(sym)
        if px <= 0:
            rows.append({"symbol": sym, "ok": False, "error": "no quote"}); continue
        cost = float(p.get("cost") or 0); vol = int(p.get("volume") or 0)
        ret = px / cost - 1 if cost else 0
        entry = p.get("entry_date") or ""
        days = 0
        try: days = (datetime.strptime(datetime.now().strftime("%Y-%m-%d"), "%Y-%m-%d") - datetime.strptime(entry, "%Y-%m-%d")).days
        except Exception: pass
        # 不设自设规则：只取 v3 agent 的 break_trigger / rebound_zone，输出信号，不自动定量/下单
        levels = v3_levels(sym, q, px)
        signal = None; status = ""
        down = None; up = None; status = ""
        if levels.get("ok"):
            d = levels.get("decision") or {}
            bt = d.get("break_trigger") or {}
            down = float(bt.get("price") or 0) or None
            status = str(bt.get("status_text") or "")[:80]
            rz = d.get("rebound_zone") or {}
            z = rz.get("zone") or []
            if len(z) == 2:
                up = float(z[1]) or None
            if up is None:
                # 用 agent 候选里的上方压力位，不手填价格
                snap = levels.get("snapshot") or {}
                cands = [c for c in (snap.get("candidates") or [])
                         if float(c.get("price") or 0) > px and str(c.get("state") or "") in ("resistance", "broken", "holding")]
                cands.sort(key=lambda c: abs(float(c.get("price") or 0) - px))
                if cands:
                    up = float(cands[0].get("price") or 0) or None
        if down is not None and px < down:
            signal = "down_break_clear"
        elif up is not None and px >= up:
            signal = "up_resistance_half"
        rec = {"symbol": sym, "theme_id": p.get("theme_id"), "cost": cost, "price": px, "ret": round(ret, 4),
               "volume": vol, "days": days, "signal": signal, "status": status, "levels": levels,
               "down_trigger": down, "up_trigger": up,
               "action": ("clear" if signal == "down_break_clear" else ("half" if signal == "up_resistance_half" else "hold")),
               "sell_volume": (vol if signal == "down_break_clear" else ((vol // 2 // 100) * 100 if signal == "up_resistance_half" else 0)),
               "note": "只输出 agent 点位信号；未自设止盈/止损/持有期规则，不自动下单"}
        rec["ok"] = True
        rows.append(rec)
    out = {"date": datetime.now().strftime("%Y%m%d"), "execute": a.execute, "rows": rows}
    p = os.path.join(ROOT, "data", "theme_sleeve_exit.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:5000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
