# -*- coding: utf-8 -*-
"""30 个 as-of case 的 AI 验证；无人工标签，只看证据/一致性/分歧。"""
from __future__ import annotations
import json, os, re, sys
from datetime import datetime
import numpy as np, pandas as pd, requests
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jobs"))
from ai_bellwether_asof import facts, PROMPT, CHAT
OUT = os.path.join(ROOT, "data/core_pit")
EV = os.path.join(OUT, "bellwether_events.csv")
N_PER = 10

def main():
    ev = pd.read_csv(EV, dtype={"date": str})
    rng = np.random.default_rng(7)
    cases=[]
    for st in ["alive","warning","dead","rotated"]:
        g=ev[ev.state==st].drop_duplicates(["theme","segment_role","date"])
        if not len(g): continue
        take=min(N_PER, len(g))
        idx=rng.choice(len(g), size=take, replace=False)
        for _,r in g.iloc[idx].iterrows(): cases.append((r.theme,r.segment_role,r.date,st))
    # 去重同 theme/date/segment
    seen=set(); uniq=[]
    for c in cases:
        k=(c[0],c[1],c[2])
        if k not in seen: seen.add(k); uniq.append(c)
    cases=uniq[:36]
    out=[]
    for theme,seg,date,mech in cases:
        f=facts(theme,date,seg)
        if not f: continue
        ai_facts={k:v for k,v in f.items() if k!="mechanical_state"}
        prompt=PROMPT.format(facts=json.dumps(ai_facts,ensure_ascii=False,indent=1))
        sid=f"val_{theme}_{seg}_{date}_{datetime.now().strftime('%H%M%S%f')}"
        try:
            r=requests.post(CHAT,json={"message":prompt,"session_id":sid},timeout=240); r.raise_for_status()
            raw=str((r.json() or {}).get("reply") or "").strip()
            txt=re.sub(r"^```(?:json)?\s*","",raw); txt=re.sub(r"\s*```\s*$","",txt)
            ans=json.loads(txt)
            out.append({"theme":theme,"segment":seg,"date":date,"mechanical":mech,
                        "ai":ans.get("state"),"confidence":ans.get("confidence"),
                        "evidence_n":len(ans.get("evidence") or []),
                        "lower_high_count":ans.get("lower_high_count"),
                        "volume_distribution":ans.get("volume_distribution"),
                        "rebound_failed":ans.get("rebound_failed"),
                        "actions":json.dumps(ans.get("action_permissions"),ensure_ascii=False)})
        except Exception as e:
            out.append({"theme":theme,"segment":seg,"date":date,"mechanical":mech,
                        "ai":"ERROR","error":str(e)[:160]})
        print(".",end="",flush=True)
    df=pd.DataFrame(out); df.to_csv(os.path.join(OUT,"ai_validation_cases.csv"),index=False,encoding="utf-8-sig")
    valid=df[df.ai!="ERROR"]
    summary={"n":len(df),"valid":len(valid),"errors":int((df.ai=="ERROR").sum()),
             "agree":int((valid.ai==valid.mechanical).sum()),
             "agree_rate":round(float((valid.ai==valid.mechanical).mean()),2) if len(valid) else 0,
             "confusion":pd.crosstab(valid.mechanical,valid.ai).reset_index().to_dict("records") if len(valid) else [],
             "by_mech":valid.groupby("mechanical").ai.value_counts().to_dict() if len(valid) else {},
             "avg_evidence":round(float(valid.evidence_n.mean()),1) if len(valid) else 0,
             "avg_conf":round(float(valid.confidence.dropna().mean()),2) if len(valid) else 0}
    json.dump(summary,open(os.path.join(OUT,"ai_validation_summary.json"),"w"),ensure_ascii=False,indent=1)
    print(); print(json.dumps(summary,ensure_ascii=False,indent=1))

if __name__=="__main__":
    main()
