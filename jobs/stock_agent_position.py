#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""stock_agent_position.py — selected → calc_position（默认 dry-run，不自动下单）。

- 读 stock_agent_selected.json；
- 对 watch/probe 调现有 /indicator/calc-position（带 theme_id/intent）；
- calc_position 内部继续走：mainline caps / thesis / P3 三仓档位 / defense / wave；
- 只有 verdict=probe 且 all_pass 且 --execute 才调 /trades（模拟盘）；
- 结果写 stock_agent_positions.json。
"""
from __future__ import annotations
import argparse, json, os, sys, time
from datetime import datetime
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def api_base():
    return (os.environ.get("MARCUS_API_URL") or "http://backend:8000/api/v1").rstrip("/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--execute", action="store_true", help="允许 probe 且 all_pass 时下模拟盘单")
    ap.add_argument("--timeout", type=int, default=180)
    a = ap.parse_args()
    base = os.path.join(ROOT, "data", "mainline", a.date)
    sp = os.path.join(base, "stock_agent_selected.json")
    # ★ 2026-11-03：原来空等 90×10s=15 分钟 ⇒ 上游没产出时通知里只剩一串 "waiting for …" 且
    #   失败点落在**下游**（2026-11-02 实况：facts 没触发 ⇒ select/position 各空等 15 分钟 rc=2 ✗）。
    #   现改为 18×10s=3 分钟并点名上游任务；链化后（jobs/stock_agent_chain.py）正常永远秒过 ✓
    for _ in range(18):
        if os.path.exists(sp):
            break
        print("waiting for P3 selected: %s（上游 stock_agent_select_daily / 链第 2 步）" % sp,
              flush=True)
        time.sleep(10)
    if not os.path.exists(sp):
        print("missing %s ⇒ 上游选股没产出（先跑 jobs/stock_agent_select.py）" % sp, flush=True)
        return 2
    sel = json.load(open(sp, encoding="utf-8"))
    out = []
    for t in sel.get("themes", []):
        tid = t.get("theme_id")
        for x in t.get("selected", []):
            if x.get("verdict") == "reject":
                continue
            body = {
                "symbol": x.get("symbol"),
                "signal_strength": "medium",
                "chain_role": "mid",
                "tier": "probe",
                "stance": "yellow",
                "intent": "probe",
                "theme_id": tid,
            }
            # 共享买入前门：P2 + B4/B5 + B8（选股后、calc_position 前）
            try:
                _g = requests.post(api_base() + "/indicator/pre-trade-gate",
                                   json={"symbol": x.get("symbol"), "intent": "probe",
                                         "source": "stock_agent_position"}, timeout=60).json()
            except Exception as _ge:
                _g = {"allow": True, "multiplier": 1.0,
                      "reasons": ["pre_trade_gate 调用失败(fail-open): " + str(_ge)[:120]]}
            if not _g.get("allow", True):
                out.append({"theme_id": tid, "symbol": x.get("symbol"), "name": x.get("name"),
                            "verdict": x.get("verdict"), "blocked": True,
                            "reasons": _g.get("reasons") or [], "gate": _g})
                continue
            _mult = float(_g.get("multiplier") or 1.0)
            if _mult < 1.0:
                body["signal_strength"] = "low"
                body["tier"] = "probe"
            try:
                r = requests.post(api_base() + "/indicator/calc-position", json=body, timeout=a.timeout)
                r.raise_for_status()
                d = r.json()
            except Exception as e:
                out.append({"theme_id": tid, "symbol": x.get("symbol"), "error": str(e)[:200]})
                continue
            q = d.get("quantity") or {}
            rec = {
                "theme_id": tid, "symbol": x.get("symbol"), "name": x.get("name"),
                "verdict": x.get("verdict"), "classification": x.get("classification"),
                "old_leader": x.get("old_leader"),
                "all_pass": bool(d.get("all_pass")),
                "single_stock_cap_pct": d.get("single_stock_cap_pct"),
                "max_shares": q.get("max_shares"), "rec_shares": q.get("rec_shares"),
                "rec_amount": q.get("rec_amount"), "current_price": d.get("current_price"),
                "mainline": d.get("mainline"),
                "warnings": (d.get("warnings") or []) + ([] if not _g.get("reasons") else ["gate: " + "；".join(_g.get("reasons") or [])]),
                "gate": {"allow": _g.get("allow"), "multiplier": _mult, "reasons": _g.get("reasons") or []},
                "order": None,
            }
            # 只有 probe + all_pass + 显式 --execute 才下模拟盘
            if a.execute and x.get("verdict") == "probe" and rec["all_pass"] and (rec["rec_shares"] or 0) > 0:
                try:
                    order = {"symbol": rec["symbol"], "side": "buy", "price": rec["current_price"],
                             "volume": rec["rec_shares"], "reason": "P3 agent probe + all_pass",
                             "intent": "probe"}
                    rr = requests.post(api_base() + "/trades", json=order, timeout=a.timeout)
                    rr.raise_for_status()
                    rec["order"] = rr.json()
                except Exception as e:
                    rec["order"] = {"error": str(e)[:200]}
            out.append(rec)
    fp = os.path.join(base, "stock_agent_positions.json")
    json.dump({"date": a.date, "dry_run": not a.execute, "positions": out}, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("theme | symbol | verdict | all_pass | rec_shares | mainline | warnings")
    for r in out:
        if r.get("blocked"):
            print(f"{r.get('theme_id')} | {r.get('symbol')} | BLOCKED | "
                  f"{'；'.join(r.get('reasons') or [])}")
            continue
        if "error" in r:
            print(r["theme_id"], r["symbol"], "ERROR", r["error"]); continue
        m = r.get("mainline") or {}
        print(f"{r['theme_id']} | {r['symbol']} {r['name']} | {r['verdict']} | {r['all_pass']} | {r['rec_shares']} | "
              f"{m.get('tradeable')}/{m.get('status')} | {'; '.join(r['warnings'][:3])}")
    print("written", fp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
