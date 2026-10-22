# -*- coding: utf-8 -*-
"""用已抽出的 36 个 case，给 AI 补 prior state / leader_changed / impulse 后重跑。"""
from __future__ import annotations
import json, os, re, sys
from datetime import datetime
import pandas as pd, requests
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jobs"))
from ai_bellwether_asof import facts, PROMPT, CHAT
OUT=os.path.join(ROOT,"data/core_pit")

def main():
    old=pd.read_csv(os.path.join(OUT,"ai_validation_cases.csv"),dtype={"date":str})
    out=[]
    for _,r in old.iterrows():
        f=facts(r.theme,r.date,r.segment)
        if not f: continue
        ai_facts={k:v for k,v in f.items() if k not in ("mechanical_state","output_mechanical_state")}
        prompt=PROMPT.format(facts=json.dumps(ai_facts,ensure_ascii=False,indent=1))
        sid=f"v2_{r.theme}_{r.segment}_{r.date}_{datetime.now().strftime('%H%M%S%f')}"
        try:
            resp=requests.post(CHAT,json={"message":prompt,"session_id":sid},timeout=240); resp.raise_for_status()
            raw=str((resp.json() or {}).get("reply") or "").strip()
            txt=re.sub(r"^```(?:json)?\s*","",raw); txt=re.sub(r"\s*```\s*$","",txt)
            ans=json.loads(txt)
            out.append({"theme":r.theme,"segment":r.segment,"date":r.date,"mechanical":r.mechanical,
                        "ai":ans.get("state"),"confidence":ans.get("confidence"),
                        "evidence":json.dumps(ans.get("evidence") or [],ensure_ascii=False),
                        "leader_changed":f.get("leader_changed"),"prev_state":f.get("previous_state"),
                        "cycle_start":f.get("cycle_start"),"last_high":f.get("last_high"),
                        "leader_ret":f.get("leader_ret_since_cycle"),"lag":f.get("second_lag")})
        except Exception as e:
            out.append({"theme":r.theme,"segment":r.segment,"date":r.date,"mechanical":r.mechanical,"ai":"ERROR","error":str(e)[:160]})
        print(".",end="",flush=True)
    df=pd.DataFrame(out); df.to_csv(os.path.join(OUT,"ai_validation_cases_v2.csv"),index=False,encoding="utf-8-sig")
    v=df[df.ai!="ERROR"]
    print(); print(json.dumps({"n":len(df),"valid":len(v),"errors":int((df.ai=='ERROR').sum()),
        "agree":int((v.ai==v.mechanical).sum()),"agree_rate":round(float((v.ai==v.mechanical).mean()),3),
        "by_mech":v.groupby(["mechanical","ai"]).size().reset_index(name="n").to_dict("records")},ensure_ascii=False,indent=1))

if __name__=="__main__": main()
