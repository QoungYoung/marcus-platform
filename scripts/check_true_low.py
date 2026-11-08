#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""check_true_low.py — 不回测胜率，检查 low 信号是不是“真低位”。

对每个 low 信号看未来 20 日 K 线：
  - future_min: 未来 20 日最低价相对信号收盘的回撤（越负=继续破低）
  - future_max: 未来 20 日最高价相对信号收盘的涨幅
  - pos250: 信号时 250 日区间位置
  - made_lower_low: 未来是否跌破信号价
只描述“位置是否真低”，不评价收益/胜率。
"""
from __future__ import annotations
import json, os, sys
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "jobs")); sys.path.insert(0, os.path.join(ROOT, "scripts"))
from backtest_low_wave import load_bars, wave_map, op_for, ml_series, active_at, load_yaml, THEME  # noqa: E402


def main():
    cfg = load_yaml(THEME)
    seeds = []
    for t in cfg.get("themes", []) + cfg.get("satellite_themes", []):
        for role, x in [("leader", t.get("leader"))] + [("ladder", y) for y in (t.get("ladder") or [])]:
            if x and x.get("code"): seeds.append((str(x["code"]), role, t["id"], t["name"]))
    bars = load_bars(); waves = wave_map(); subs, bench = ml_series()
    all_dates = sorted({d for g in bars.values() for d in g["trade_date"]})
    dates = [d for d in all_dates if "20260201" <= d <= "20260814"][::5]
    rows = []
    for d in dates:
        op = op_for(d, waves)
        if op not in ("build", "side", "t_only"): continue
        if not active_at(d, subs, bench): continue
        for code, role, tid, tname in seeds:
            g = bars.get(code)
            if g is None: continue
            i = g["trade_date"].searchsorted(d, side="right") - 1
            if i < 120 or i + 20 >= len(g): continue
            hist = g.iloc[i-119:i+1]; close = float(g.iloc[i]["close"])
            hi = float(hist["high"].max()); lo = float(hist["low"].min())
            if hi <= lo or close <= 0: continue
            pos = (close-lo)/(hi-lo); dh = close/hi-1; r60 = close/float(g.iloc[i-60]["close"])-1
            if not (dh <= -0.30 and pos <= 0.35 and r60 <= 0): continue
            fut = g.iloc[i+1:i+21]
            fmin = float(fut["low"].min())/close - 1
            fmax = float(fut["high"].max())/close - 1
            h250 = g.iloc[max(0, i-249):i+1]
            hi250 = float(h250["high"].max()); lo250 = float(h250["low"].min())
            pos250 = (close-lo250)/(hi250-lo250) if hi250 > lo250 else 0.5
            rows.append({"date": d, "op": op, "code": code, "role": role, "theme": tid,
                         "pos120": round(pos, 3), "pos250": round(pos250, 3),
                         "future_min": round(fmin, 4), "future_max": round(fmax, 4),
                         "made_lower_low": bool(fmin < 0)})
    r = pd.DataFrame(rows)
    out = {"n": len(r), "summary": {}, "by_role": {}, "examples": []}
    if len(r):
        for label, mask in [("all_low", pd.Series(True, index=r.index)), ("leader", r.role == "leader"), ("ladder", r.role == "ladder")]:
            x = r[mask]
            if not len(x): continue
            out["by_role"][label] = {
                "n": int(len(x)),
                "made_lower_low_pct": round(float(x.made_lower_low.mean()), 3),
                "future_min_median": round(float(x.future_min.median()), 4),
                "future_min_q25": round(float(x.future_min.quantile(.25)), 4),
                "future_max_median": round(float(x.future_max.median()), 4),
                "pos250_median": round(float(x.pos250.median()), 3),
                "pos250_below_035_pct": round(float((x.pos250 < 0.35).mean()), 3),
            }
        out["summary"] = out["by_role"].get("all_low", {})
        out["examples"] = r.sort_values("future_min").head(10).to_dict("records")
    json.dump(out, open(os.path.join(ROOT, "data", "true_low_check.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
