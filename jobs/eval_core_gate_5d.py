# -*- coding: utf-8 -*-
"""只按 5 日验收 core 动作闸：合规 + blocked vs allowed。"""
from __future__ import annotations
import json, os
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data/core_pit")

def st(x):
    x = pd.Series(x).dropna()
    if not len(x): return {"n": 0}
    return {"n": int(len(x)), "mean": round(float(x.mean())*100, 3), "win": round(float((x>0).mean()),3),
            "t": round(float(x.mean()/(x.std(ddof=1)/np.sqrt(len(x)))),2) if len(x)>2 and x.std(ddof=1)>0 else None}

def main():
    p = pd.read_csv(os.path.join(OUT,"picks.csv"), dtype={"date":str})
    # compliance
    dead_second = int(((p.leader_state=="dead") & (p.role=="second") & p.allow).sum())
    warn_second = int(((p.leader_state=="warning") & (p.role=="second") & p.allow).sum())
    daily = p.groupby("date").apply(lambda g: pd.Series({
        "A": g.f5.mean(),
        "B": g.loc[g.allow,"f5"].sum()/max(len(g),1),
        "n": len(g), "n_gate": int(g.allow.sum())})).reset_index()
    blocks=[daily.iloc[i:i+4] for i in range(0,len(daily),4)]
    rng=np.random.default_rng(42)
    def boot(col):
        vals=[]
        for _ in range(3000):
            pick=rng.integers(0,len(blocks),len(blocks)); x=pd.concat([blocks[i] for i in pick])[col].dropna(); vals.append(float(x.mean()))
        return {"mean":round(float(daily[col].mean())*100,3),"ci95":[round(float(np.percentile(vals,2.5))*100,3),round(float(np.percentile(vals,97.5))*100,3)],"p_gt0":round(float((np.array(vals)>0).mean()),3)}
    out={"n_dates":len(daily),"n_picks":len(p),"compliance":{"dead_second":dead_second,"warning_second":warn_second},
         "allowed":st(p[p.allow].f5),"blocked":st(p[~p.allow].f5),
         "A":boot("A"),"B":boot("B"),
         "by_state":p.groupby(["leader_state","allow"]).f5.agg(["count","mean"]).round(4).reset_index().to_dict("records")}
    json.dump(out,open(os.path.join(OUT,"gate_5d.json"),"w"),ensure_ascii=False,indent=1)
    print(json.dumps(out,ensure_ascii=False,indent=1))
    p.to_csv(os.path.join(OUT,"picks_5d.csv"),index=False,encoding="utf-8-sig")
    daily.to_csv(os.path.join(OUT,"daily_5d.csv"),index=False,encoding="utf-8-sig")

if __name__=="__main__":
    main()
