# -*- coding: utf-8 -*-
"""PIT core 回测的块状 bootstrap：按 4 周块重采样。"""
from __future__ import annotations
import json, os
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data/core_pit")
rng = np.random.default_rng(42)

def main():
    p = pd.read_csv(os.path.join(OUT, "picks.csv"), dtype={"date": str})
    t = pd.read_csv(os.path.join(OUT, "themes.csv"), dtype={"date": str})
    daily = p.groupby("date").apply(lambda g: pd.Series({
        "A": g.f20.mean(), "B": np.average(g.f20, weights=g.mult), "n": len(g)})).reset_index()
    daily = daily.merge(t.groupby("date").f20.mean().rename("C"), on="date", how="left").sort_values("date")
    daily.to_csv(os.path.join(OUT, "daily.csv"), index=False, encoding="utf-8-sig")
    # 4-week blocks
    blocks = [daily.iloc[i:i+4] for i in range(0, len(daily), 4)]
    def boot(col):
        vals = []
        for _ in range(3000):
            pick = rng.integers(0, len(blocks), len(blocks))
            x = pd.concat([blocks[i] for i in pick])[col].dropna()
            vals.append(float(x.mean()))
        vals = np.array(vals)
        return {"mean": round(float(daily[col].mean())*100, 3),
                "ci95": [round(float(np.percentile(vals,2.5))*100,3), round(float(np.percentile(vals,97.5))*100,3)],
                "p_gt0": round(float((vals > 0).mean()), 3)}
    out = {c: boot(c) for c in ("A","B","C")}
    # paired differences on same resampled blocks
    diffs = {}
    for name, a, b in [("A-C","A","C"), ("B-A","B","A")]:
        vals=[]
        for _ in range(3000):
            pick = rng.integers(0, len(blocks), len(blocks))
            x = pd.concat([blocks[i] for i in pick])
            vals.append(float((x[a]-x[b]).mean()))
        vals=np.array(vals)
        diffs[name]={"mean": round(float((daily[a]-daily[b]).mean())*100,3),
                     "ci95":[round(float(np.percentile(vals,2.5))*100,3),round(float(np.percentile(vals,97.5))*100,3)],
                     "p_gt0":round(float((vals>0).mean()),3)}
    json.dump({"bootstrap":out,"diff":diffs,"n_dates":len(daily),"blocks":len(blocks)},
              open(os.path.join(OUT,"bootstrap.json"),"w"),ensure_ascii=False,indent=1)
    print(json.dumps({"bootstrap":out,"diff":diffs,"n_dates":len(daily),"blocks":len(blocks)},ensure_ascii=False,indent=1))

if __name__ == "__main__":
    main()
