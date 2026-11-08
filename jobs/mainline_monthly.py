#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mainline_monthly.py — P1 L0 月度主线（机械评分，不用 agent）。

输入：config/mainline/universe_mainline.yaml
输出：data/mainline/{as_of}/mainline.json

逻辑：
  - 取最近 20 个交易日；代理指数 20 日收益减去上证 20 日收益 = 月度超额；
  - 每个 sub_mainline 的代理等权平均；
  - 超额最高的 sub_mainline 作为本月子主线；>0 且覆盖>=80% 记 confirmed。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "jobs"))

from mainline_common import index_close, last_trade_date, pct, trade_dates, write_json  # noqa: E402

CFG = os.path.join(ROOT, "config", "mainline", "universe_mainline.yaml")


def load_yaml(path: str):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    a = ap.parse_args()
    date8 = last_trade_date(a.date)
    dates = trade_dates(a.date)
    d20 = dates[-21] if len(dates) >= 21 else dates[0]
    cfg = load_yaml(CFG)

    # benchmark
    bclose = index_close("csi", "000001.SH", d20, date8)
    bret = pct(float(bclose.iloc[-1]), float(bclose.iloc[0])) if len(bclose) >= 2 else 0.0

    rows = []
    for m in cfg.get("mainlines") or []:
        if m.get("status") != "active":
            continue
        subs = []
        for s in m.get("sub_mainlines") or []:
            rets = []
            used = []
            for p in s.get("proxies") or []:
                if isinstance(p, str):
                    code = p
                    typ = "sw_l2" if code.endswith(".SI") else ("eastmoney" if code.startswith("BK") else "csi")
                else:
                    code = str(p.get("code"))
                    typ = str(p.get("source") or "csi")
                try:
                    c = index_close(typ, code, d20, date8)
                    if len(c) >= 2:
                        rets.append(pct(float(c.iloc[-1]), float(c.iloc[0])))
                        used.append(code)
                except Exception:
                    continue
            avg = sum(rets) / len(rets) if rets else 0.0
            subs.append({"id": s.get("id"), "name": s.get("name"), "ret20": round(avg, 4),
                         "excess": round(avg - bret, 4), "proxies_used": used,
                         "coverage": round(len(used) / max(1, len(s.get("proxies") or [])), 2),
                         "valid_proxies": len(used), "total_proxies": len(s.get("proxies") or [])})
        subs.sort(key=lambda x: -x["excess"])
        best = subs[0] if subs else None
        rows.append({
            "mainline": m.get("id"), "name": m.get("name"),
            "sub_mainlines": subs,
            "best_sub": best,
            "abs_ok": bool(best and best["ret20"] > 0),
            "rel_ok": bool(best and best["excess"] > 0),
            "cov_ok": bool(best and best["coverage"] >= 0.8),
            "confirmed": bool(best and best["ret20"] > 0 and best["excess"] > 0 and best["coverage"] >= 0.8),
        })

    out = {
        "as_of": date8, "freq": "monthly", "benchmark": "000001.SH",
        "benchmark_ret20": round(bret, 4),
        "mainline": rows[0]["mainline"] if rows else None,
        "name": rows[0]["name"] if rows else None,
        "sub_mainline": rows[0]["best_sub"]["id"] if rows and rows[0]["best_sub"] else None,
        "sub_mainline_name": rows[0]["best_sub"]["name"] if rows and rows[0]["best_sub"] else None,
        "strength": rows[0]["best_sub"]["excess"] if rows and rows[0]["best_sub"] else 0.0,
        "confirmed": bool(rows and rows[0]["confirmed"]),
        "abs_ok": bool(rows and rows[0].get("abs_ok")),
        "rel_ok": bool(rows and rows[0].get("rel_ok")),
        "cov_ok": bool(rows and rows[0].get("cov_ok")),
        "data_warning": bool(rows and rows[0]["best_sub"] and rows[0]["best_sub"]["coverage"] < 1.0),
        "mainline_exists": bool(rows),
        "mainline_active": bool(rows and rows[0].get("abs_ok") and rows[0].get("rel_ok")),
        "tradeable": bool(rows and rows[0]["confirmed"]),
        "status": ("confirmed" if rows and rows[0]["confirmed"] else
                   "pullback" if rows and rows[0].get("abs_ok") else "weak"),
        "regime": ("up" if rows and rows[0]["best_sub"] and rows[0]["best_sub"]["excess"] > 0
                   else "pullback" if rows and rows[0]["best_sub"] and rows[0]["best_sub"]["excess"] >= -0.08
                   else "weak"),
        "detail": rows,
    }
    path = os.path.join(ROOT, "data", "mainline", date8, "mainline.json")
    write_json(path, out)
    print(f"as_of={date8} mainline={out['name']} sub={out['sub_mainline_name']} "
          f"excess={out['strength']} exists={out['mainline_exists']} active={out['mainline_active']} tradeable={out['tradeable']} status={out['status']}")
    for r in rows:
        print(f"  {r['name']}: " + ", ".join(f"{s['name']}={s['excess']:+.2%}" for s in r["sub_mainlines"]))
    print(f"written: {path}")


if __name__ == "__main__":
    main()
