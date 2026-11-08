#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""position_case_eval.py — 用个股 K 线验证 high/low 判例。

只对 label_type=price_low/price_high 的样本计准确率；
sector_low / valuation_low / laggard_low 只显示 K 线结果，不计分。
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "jobs"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from mainline_common import relay_query  # noqa: E402
from position_rules import load_cfg  # noqa: E402


def kline_state(code: str, date8: str, cfg: dict):
    start = (datetime.strptime(date8, "%Y%m%d") - timedelta(days=300)).strftime("%Y%m%d")
    d = relay_query("daily", ts_code=code, start_date=start, end_date=date8)
    if d is None or len(d) < 2:
        return None, {"error": "no data"}
    d = d.sort_values("trade_date")
    close = float(d.iloc[-1]["close"])
    hi = float(d.tail(120)["high"].max())
    lo = float(d.tail(120)["low"].min())
    pos = (close - lo) / (hi - lo) if hi > lo else 0.5
    dh = close / hi - 1
    r60 = close / float(d.iloc[-61]["close"]) - 1 if len(d) >= 61 else 0.0
    th = cfg.get("thresholds") or {}
    low = th.get("low") or {"dist_high120_max": -0.30, "range_pos_120_max": 0.35, "ret60_max": 0.0}
    high = th.get("high") or {"dist_high120_min": -0.10, "range_pos_120_min": 0.65, "ret60_min": 0.0, "range_pos_120_hard": 0.80}
    if dh <= float(low["dist_high120_max"]) and pos <= float(low["range_pos_120_max"]) and r60 <= float(low["ret60_max"]):
        state = "low"
    elif ((dh >= float(high["dist_high120_min"]) and pos >= float(high["range_pos_120_min"]) and r60 >= float(high["ret60_min"]))
          or pos >= float(high["range_pos_120_hard"])):
        state = "high"
    else:
        state = "mid"
    return state, {"close": close, "pos120": round(pos, 3), "dist_high120": round(dh, 4), "ret60": round(r60, 4)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=os.path.join(ROOT, "data", "position_cases.jsonl"))
    a = ap.parse_args()
    cfg = load_cfg()
    rows = []
    for line in open(a.cases, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        c = json.loads(line)
        date8 = c["date"].replace("-", "")
        state, feat = kline_state(c["symbol"], date8, cfg)
        comparable = c.get("label_type") in ("price_low", "price_high")
        match = (state == c["label"]) if comparable and state else None
        rows.append({**c, "kline_state": state, "features": feat, "comparable": comparable, "match": match})
    price = [r for r in rows if r["comparable"]]
    ok = sum(1 for r in price if r["match"])
    print("date | symbol | label | type | K线 | match")
    print("---|---|---|---|---|---")
    for r in rows:
        print(f"{r['date']} | {r['symbol']} {r['name']} | {r['label']} | {r['label_type']} | {r['kline_state']} | "
              f"{'OK' if r['match'] else ('MISS' if r['match'] is False else 'SKIP')}")
        print("  ", r["features"])
    print(f"\nprice_low/price_high: {ok}/{len(price)}")
    out = {"cases": rows, "price_accuracy": {"ok": ok, "n": len(price)}}
    p = os.path.join(ROOT, "data", "position_case_eval.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("written:", p)


if __name__ == "__main__":
    main()
