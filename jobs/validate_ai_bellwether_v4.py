# -*- coding: utf-8 -*-
"""AI 提示词 v4：用 cycle 级 lower_high_count / event 放量，不再只看 20 日。"""
from __future__ import annotations
import json, os, re, sys
from datetime import datetime
import pandas as pd, requests
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0,os.path.join(ROOT,"jobs"))
from ai_bellwether_asof import facts, CHAT
OUT=os.path.join(ROOT,"data/core_pit")
PROMPT="""你是风向标裁判。按下面顺序判断，不做收益预测，不给买卖建议。

事实中：
- event_lower_high_count 是本轮 cycle 内的高点下移次数；
- event_volume_distribution 是事件层是否放量出货；
- consecutive_lower_highs 只是最近 20 日，不是 cycle 全量；
- previous_state/leader_changed 用于 rotated。

顺序：
1. leader_changed=true 且 previous_state=dead 且新 leader 有独立 impulse → rotated；
   若 previous_state 不是 dead，禁止 rotated。
2. close_vs_last_high >= -0.02 且 event_volume_distribution=false → alive。
3. event_lower_high_count >= 2 且 event_volume_distribution=true → dead。
4. 其余 → warning。
5. 无 leader → unknown。

禁止：
- 只看最近 20 日 lower highs；
- 没放量就一律 warning；
- leader_changed 就判 rotated；
- 跌一天/跌 10% 判 dead。

只输出 JSON：
{{"theme":"...","date":"...","state":"alive|warning|dead|rotated|unknown","confidence":0.0,
"lower_high_count":0,"volume_distribution":true,"rebound_failed":null,
"evidence":["引用具体数字"],"action_permissions":{{"second_catchup":false,"high_cut_low":false,"hold_second":false}}}}

=== AS-OF FACTS ===
{facts}
=== END ===
"""
def main():
    old=pd.read_csv(os.path.join(OUT,"ai_validation_cases.csv"),dtype={"date":str}); out=[]
    for _,r in old.iterrows():
        f=facts(r.theme,r.date,r.segment)
        if not f: continue
        ai_facts={k:v for k,v in f.items() if k not in ("mechanical_state","output_mechanical_state")}
        prompt=PROMPT.format(facts=json.dumps(ai_facts,ensure_ascii=False,indent=1))
        sid=f"v4_{r.theme}_{r.segment}_{r.date}_{datetime.now().strftime('%H%M%S%f')}"
        try:
            resp=requests.post(CHAT,json={"message":prompt,"session_id":sid},timeout=240); resp.raise_for_status()
            raw=str((resp.json() or {}).get("reply") or "").strip(); txt=re.sub(r"^```(?:json)?\s*","",raw); txt=re.sub(r"\s*```\s*$","",txt)
            ans=json.loads(txt)
            out.append({"theme":r.theme,"segment":r.segment,"date":r.date,"mechanical":r.mechanical,"ai":ans.get("state"),
                        "confidence":ans.get("confidence"),"evidence":json.dumps(ans.get("evidence") or [],ensure_ascii=False),
                        "event_lower":f.get("event_lower_high_count"),"event_vol":f.get("event_volume_distribution"),
                        "close_vs_high":f.get("close_vs_last_high"),"prev_state":f.get("previous_state")})
        except Exception as e:
            out.append({"theme":r.theme,"segment":r.segment,"date":r.date,"mechanical":r.mechanical,"ai":"ERROR","error":str(e)[:160]})
        print(".",end="",flush=True)
    df=pd.DataFrame(out); df.to_csv(os.path.join(OUT,"ai_validation_cases_v4.csv"),index=False,encoding="utf-8-sig")
    v=df[df.ai!="ERROR"]; print(); print(pd.crosstab(v.mechanical,v.ai).to_string());
    print(json.dumps({"n":len(df),"valid":len(v),"errors":int((df.ai=='ERROR').sum()),"agree":int((v.ai==v.mechanical).sum()),
                      "agree_rate":round(float((v.ai==v.mechanical).mean()),3)},ensure_ascii=False))
if __name__=="__main__": main()
