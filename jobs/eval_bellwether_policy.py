# -*- coding: utf-8 -*-
"""按“合规 + 风险”口径评估风向标 overlay，不把收益当主指标。"""
from __future__ import annotations
import json, os
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data/core_pit")

def risk(x):
    x = pd.Series(x).dropna() * 100
    if not len(x): return {}
    cum = (1 + x / 100).cumprod()
    return {"n": int(len(x)), "mean": round(float(x.mean()), 2),
            "vol": round(float(x.std()), 2),
            "downside": round(float(x[x < 0].std()), 2),
            "max_dd": round(float((cum / cum.cummax() - 1).min() * 100), 2),
            "hit": round(float((x > 0).mean()), 3)}

def main():
    p = pd.read_csv(os.path.join(OUT, "picks.csv"), dtype={"date": str})
    d = pd.read_csv(os.path.join(OUT, "daily.csv"), dtype={"date": str})
    out = {"n_picks": len(p), "n_dates": int(p.date.nunique())}
    out["role_state"] = p.groupby(["leader_state", "role"]).f20.agg(["count", "mean"]).round(4).reset_index().to_dict("records")
    out["forbidden"] = {
        "dead_second": int(((p.leader_state == "dead") & (p.role == "second")).sum()),
        "warning_second": int(((p.leader_state == "warning") & (p.role == "second")).sum()),
        "dead_or_warning_positive": int((p.leader_state.isin(["dead", "warning"]) & (p.f20 > 0)).sum()),
        "dead_or_warning_total": int(p.leader_state.isin(["dead", "warning"]).sum()),
    }
    out["risk"] = {c: risk(d[c]) for c in ("A", "B", "C") if c in d}
    out["bootstrap"] = json.load(open(os.path.join(OUT, "bootstrap.json"), encoding="utf-8")) if os.path.exists(os.path.join(OUT, "bootstrap.json")) else {}
    json.dump(out, open(os.path.join(OUT, "policy.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
