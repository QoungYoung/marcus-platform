# -*- coding: utf-8 -*-
"""AI 提示词 v3：决策树 + 连续 lower high + 换人 guard；同样 36 case。"""
from __future__ import annotations
import json, os, re, sys
from datetime import datetime
import pandas as pd, requests
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jobs"))
from ai_bellwether_asof import facts, CHAT
OUT=os.path.join(ROOT,"data/core_pit")

PROMPT = """你是风向标裁判。只按下面顺序做状态判断，不做收益预测，不给买卖建议。

必须先看这些事实：
- previous_state：上一期状态
- leader_changed：龙头是否换人
- consecutive_lower_highs：最近连续 lower high 次数
- volume_distribution：是否放量下跌
- close_vs_last_high：现价相对上一周期 last_high
- leader_ret_since_cycle：龙头自 cycle_start 的涨幅
- second_lag：二线滞后
- seconds：二线候选

判定顺序（必须按顺序，不能跳）：
1. 若 leader_changed=true：
   - 只有 previous_state=dead 且新 leader 有独立 impulse，才判 rotated；
   - 若 previous_state 不是 dead，禁止判 rotated，继续看 2/3/4。
2. 若 close_vs_last_high >= -0.02 且 volume_distribution=false：
   → alive。
3. 若 consecutive_lower_highs >= 2 且 volume_distribution=true：
   → dead。
4. 其余：
   → warning。
5. 若没有 leader/cycle：
   → unknown。

禁止：
- 不能因为“没有放量”就一律 warning；
- 不能因为“跌一天/跌 10%”判 dead；
- 不能因为 leader_changed 就判 rotated；
- 不能为了保守把 alive 判成 warning。

只输出 JSON：
{{"theme":"...","date":"...","state":"alive|warning|dead|rotated|unknown","confidence":0.0,
"lower_high_count":0,"volume_distribution":true,"rebound_failed":null,
"evidence":["必须引用具体数字"],"action_permissions":{{"second_catchup":false,"high_cut_low":false,"hold_second":false}}}}

=== AS-OF FACTS ===
{facts}
=== END ===
"""

def main():
    old=pd.read_csv(os.path.join(OUT,"ai_validation_cases.csv"),dtype={"date":str})
    out=[]
    for _,r in old.iterrows():
        f=facts(r.theme,r.date,r.segment)
        if not f: continue
        ai_facts={k:v for k,v in f.items() if k not in ("mechanical_state","output_mechanical_state")}
        prompt=PROMPT.format(facts=json.dumps(ai_facts,ensure_ascii=False,indent=1))
        sid=f"v3_{r.theme}_{r.segment}_{r.date}_{datetime.now().strftime('%H%M%S%f')}"
        try:
            resp=requests.post(CHAT,json={"message":prompt,"session_id":sid},timeout=240); resp.raise_for_status()
            raw=str((resp.json() or {}).get("reply") or "").strip()
            txt=re.sub(r"^```(?:json)?\s*","",raw); txt=re.sub(r"\s*```\s*$","",txt)
            ans=json.loads(txt)
            out.append({"theme":r.theme,"segment":r.segment,"date":r.date,"mechanical":r.mechanical,
                        "ai":ans.get("state"),"confidence":ans.get("confidence"),
                        "evidence":json.dumps(ans.get("evidence") or [],ensure_ascii=False),
                        "leader_changed":f.get("leader_changed"),"prev_state":f.get("previous_state"),
                        "consecutive_lower_highs":f.get("consecutive_lower_highs"),
                        "volume_distribution":f.get("volume_distribution"),
                        "close_vs_last_high":f.get("close_vs_last_high")})
        except Exception as e:
            out.append({"theme":r.theme,"segment":r.segment,"date":r.date,"mechanical":r.mechanical,"ai":"ERROR","error":str(e)[:160]})
        print(".",end="",flush=True)
    df=pd.DataFrame(out); df.to_csv(os.path.join(OUT,"ai_validation_cases_v3.csv"),index=False,encoding="utf-8-sig")
    v=df[df.ai!="ERROR"]
    print(); print(pd.crosstab(v.mechanical,v.ai).to_string())
    print(json.dumps({"n":len(df),"valid":len(v),"errors":int((df.ai=='ERROR').sum()),
        "agree":int((v.ai==v.mechanical).sum()),"agree_rate":round(float((v.ai==v.mechanical).mean()),3)},ensure_ascii=False))

if __name__=="__main__": main()
