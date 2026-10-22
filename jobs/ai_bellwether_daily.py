# -*- coding: utf-8 -*-
"""生产 AI 风向标旁路：45 个子主题，as-of 事实 → AI 结构化状态；只写旁路，不参与选股。"""
from __future__ import annotations
import argparse, json, os, re, sys
from datetime import datetime
import numpy as np, pandas as pd, requests
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(ROOT,"backend"),os.path.join(ROOT,"backend/app/services"),
          os.path.join(ROOT,"app/services"),os.path.join(ROOT,"app"),ROOT,os.path.join(ROOT,"jobs")):
    if os.path.isdir(p): sys.path.insert(0,p)
from wolf_card.subthemes import stocks as sub_stocks, parent
from bars_source import load_bars
OUT=os.path.join(ROOT,"data/core_p1"); os.makedirs(OUT,exist_ok=True)
STATE=os.path.join(OUT,"bellwether_state_latest.json")
AI_OUT=os.path.join(OUT,"ai_state_latest.json")
CHAT=os.environ.get("AI_BELLWETHER_CHAT") or os.environ.get("BELLWETHER_CHAT_URL") or "http://marcus-dsh:3001/chat"

PROMPT="""你是生产环境的 A 股风向标裁判。按下面规则判断每个子主题，只输出 JSON。

规则：
1. 若 leader_changed=true 且 previous_state=dead 且新 leader 有独立 impulse → rotated；
   若 previous_state 不是 dead，禁止 rotated。
2. close_vs_last_high >= -0.02 且 volume_distribution=false → alive。
3. consecutive_lower_highs >= 2 且 volume_distribution=true → dead。
4. 其余 → warning。
5. 无 leader/cycle → unknown。

禁止：
- 只看单日跌幅判 dead；
- 没有放量就一律 warning；
- leader_changed 就判 rotated；
- 做收益预测、给买卖建议。

输出：
{{"date":"{date}","items":[{{"subtheme":"...","state":"alive|warning|dead|rotated|unknown",
"confidence":0.0,"evidence":["引用数字"],"action_permissions":{{"second_catchup":false,"high_cut_low":false,"hold_second":false}}}}]}}

必须覆盖输入里的每一个子主题。不确定时 state=unknown。

=== ITEMS ===
{items}
=== END ===
"""

def build_packet():
    st=json.load(open(STATE,encoding="utf-8")); date=str(st.get("date")); items=st.get("items") or []
    try:
        bars=pd.read_parquet(os.path.join(ROOT,"data/bars_api.parquet"),
                             columns=["ts_code","trade_date","close","high","amount"])
    except Exception:
        bars=load_bars(min_date="20260601")
    close=bars.pivot_table(index="trade_date",columns="ts_code",values="close",aggfunc="last").sort_index()
    high=bars.pivot_table(index="trade_date",columns="ts_code",values="high",aggfunc="last").sort_index()
    amt=bars.pivot_table(index="trade_date",columns="ts_code",values="amount",aggfunc="last").sort_index()
    # 一次取上一交易日状态，避免 45 次连接
    prev_map={}
    try:
        if os.environ.get("AI_SKIP_DB"): raise RuntimeError("skip db")
        from app.database import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            row=conn.execute(text("SELECT max(trade_date) FROM bellwether_daily WHERE trade_date < :d"),{"d":date}).scalar()
            if row:
                for r in conn.execute(text("SELECT subtheme,state,bellwether_code FROM bellwether_daily WHERE trade_date=:d"),{"d":str(row)}).mappings():
                    prev_map[str(r["subtheme"])]={"state":r["state"],"code":r["bellwether_code"]}
    except Exception:
        pass
    out=[]
    for it in items:
        sub=it.get("subtheme"); code=it.get("bellwether_code")
        if not sub or not code or code not in close.columns: continue
        c=close[code].dropna().tail(60); h=high[code].reindex(c.index).dropna(); a=amt[code].reindex(c.index).dropna()
        if len(c)<25: continue
        last=float(c.iloc[-1]); ma20=float(c.tail(20).mean()); hh20=float(h.tail(20).max()); low20=float(c.tail(20).min())
        hv=[float(x) for x in h.tail(20).tolist()]; consec=0
        for i in range(len(hv)-1,0,-1):
            if hv[i] < hv[i-1]: consec+=1
            else: break
        vol=float(a.tail(5).mean()/max(a.tail(20).mean(),1))
        vol_dist=bool(vol>1.2 and last < float(c.iloc[-2]))
        ret=float(last/c.iloc[0]-1) if len(c)>1 else None
        prev=prev_map.get(str(sub), {})
        prev_state=prev.get("state"); prev_leader=prev.get("code")
        out.append({"subtheme":sub,"leader":code,"leader_name":it.get("bellwether_name") or code,
                    "mechanical_state":it.get("state"),"previous_state":prev_state,"prev_leader":prev_leader,
                    "leader_changed":bool(prev_leader and prev_leader!=code),
                    "last_close":round(last,2),"ma20":round(ma20,2),"high20":round(hh20,2),"low20":round(low20,2),
                    "consecutive_lower_highs":consec,"vol_ratio5_20":round(vol,2),
                    "volume_distribution":vol_dist,"close_vs_last_high":round(last/max(hh20,1e-9)-1,4),
                    "leader_ret_since_cycle":None if ret is None else round(ret,4),
                    "seconds":[{"code":str(r.ts_code),"name":r.get("name")} for _,r in
                               pd.DataFrame({"ts_code":sorted(sub_stocks(sub))}).head(5).iterrows()]})
    return date,out

def run_ai(date,items):
    prompt=PROMPT.format(date=date,items=json.dumps(items,ensure_ascii=False,indent=1))
    sid=f"prod_ai_{date}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    r=requests.post(CHAT,json={"message":prompt,"session_id":sid},timeout=900); r.raise_for_status()
    raw=str((r.json() or {}).get("reply") or "").strip()
    txt=re.sub(r"^```(?:json)?\s*","",raw); txt=re.sub(r"\s*```\s*$","",txt)
    return json.loads(txt)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--dry",action="store_true"); ap.add_argument("--apply",action="store_true"); a=ap.parse_args()
    date,items=build_packet(); print(json.dumps({"date":date,"items":len(items)},ensure_ascii=False))
    if a.dry:
        json.dump({"date":date,"items":items},open(AI_OUT,"w",encoding="utf-8"),ensure_ascii=False,indent=1); return
    ans=run_ai(date,items); by={x.get("subtheme"):x for x in (ans.get("items") or [])}
    # 一次取上一交易日状态，避免 45 次连接
    prev_map={}
    try:
        if os.environ.get("AI_SKIP_DB"): raise RuntimeError("skip db")
        from app.database import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            row=conn.execute(text("SELECT max(trade_date) FROM bellwether_daily WHERE trade_date < :d"),{"d":date}).scalar()
            if row:
                for r in conn.execute(text("SELECT subtheme,state,bellwether_code FROM bellwether_daily WHERE trade_date=:d"),{"d":str(row)}).mappings():
                    prev_map[str(r["subtheme"])]={"state":r["state"],"code":r["bellwether_code"]}
    except Exception:
        pass
    out=[]
    for it in items:
        x=by.get(it["subtheme"]) or {}
        out.append({**it,"ai_state":x.get("state","unknown"),"ai_confidence":x.get("confidence"),
                    "ai_evidence":x.get("evidence") or [],"ai_actions":x.get("action_permissions") or {}})
    doc={"date":date,"source":"dsh_ai_asof","usage":"shadow_only","items":out}
    json.dump(doc,open(AI_OUT,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
    print(json.dumps({"date":date,"n":len(out),"ai_states":pd.Series([x["ai_state"] for x in out]).value_counts().to_dict()},ensure_ascii=False))

if __name__=="__main__": main()
