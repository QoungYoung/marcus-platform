# -*- coding: utf-8 -*-
"""AI 风向标 as-of 试跑：只喂截至当日的事实，输出结构化判断；不参与生产。"""
from __future__ import annotations
import argparse, json, os, re, sys
from datetime import datetime
import numpy as np, pandas as pd, requests
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data/core_pit")
LAB = os.environ.get("PIT_LABELS") or os.path.join(ROOT, "data/pit/chain_map/labels_weekly.csv")
BARS = os.path.join(ROOT, "data/bars_api.parquet")
EV = os.path.join(OUT, "bellwether_events.csv")
CHAT = os.environ.get("AI_BELLWETHER_CHAT") or os.environ.get("BELLWETHER_CHAT_URL") or "http://127.0.0.1:3001/chat"

def facts(theme, date, seg=None):
    lab = pd.read_csv(LAB, dtype={"date": str, "ts_code": str})
    day = lab[(lab.date == date) & (lab.theme == theme)]
    if seg: day = day[day.segment_role == seg]
    if not len(day):
        # theme may only exist as segment parent; pick first
        day = lab[(lab.date == date) & (lab.theme == theme)]
    if not len(day): return None
    segs = sorted(day.segment_role.unique())
    seg = seg or ("seg_hydro" if "seg_hydro" in segs else segs[0])
    g = day[day.segment_role == seg]
    lead = g[g["rank"] == 1].drop_duplicates("ts_code")
    if not len(lead): return None
    code = str(lead.iloc[0].ts_code)
    bars = pd.read_parquet(BARS, columns=["ts_code","trade_date","close","high","low","amount"])
    b = bars[bars.ts_code == code].sort_values("trade_date")
    b = b[b.trade_date.astype(str) <= date].tail(60)
    if len(b) < 25: return None
    close = b.close; high = b.high; amt = b.amount
    last = close.iloc[-1]; ma20 = close.tail(20).mean()
    hh = high.tail(20).max(); lh = high.iloc[-10:-1].max()
    lower = int((high.tail(20).diff() < 0).sum())
    vol_ratio = float(amt.tail(5).mean() / max(amt.tail(20).mean(), 1))
    # 机械状态（只作参照，不喂给 AI 判断结论）
    mech = "unknown"
    if os.path.exists(EV):
        e = pd.read_csv(EV, dtype={"date": str})
        x = e[(e.date == date) & (e.theme == theme) & (e.segment_role == seg)]
        if len(x): mech = str(x.iloc[0].state)
    # 二线
    sec = g[g.bucket == "second"].drop_duplicates("ts_code").head(5)
    seconds = []
    for _, s in sec.iterrows():
        sb = bars[(bars.ts_code == str(s.ts_code)) & (bars.trade_date.astype(str) <= date)].tail(25)
        if len(sb) >= 21:
            seconds.append({"code": str(s.ts_code), "name": s.get("name"),
                            "ret5": round(float(sb.close.iloc[-1]/sb.close.iloc[-6]-1)*100,2),
                            "ret20": round(float(sb.close.iloc[-1]/sb.close.iloc[-21]-1)*100,2)})
    return {"theme": theme, "segment": seg, "date": date, "leader": code,
            "leader_name": lead.iloc[0].get("name"),
            "last_close": round(float(last),2), "ma20": round(float(ma20),2),
            "high20": round(float(hh),2), "low20": round(float(close.tail(20).min()),2),
            "lower_high_days": lower, "vol_ratio5_20": round(vol_ratio,2),
            "mechanical_state": mech, "seconds": seconds,
            "last30": [{"d": str(r.trade_date), "c": round(float(r.close),2),
                        "h": round(float(r.high),2), "amt": round(float(r.amount),1)}
                       for _, r in b.tail(30).iterrows()]}

PROMPT = """你是风向标裁判，只按下面的狼大规则判断，不做收益预测，不给买卖建议。

规则：
- 龙头放量/相对强 → impulse；
- 龙头一次比一次低 + 放量出货 → dead；
- dead 后不能高切低、不能补涨；
- alive 时自己的票没涨，可以拿着等补涨；
- 龙头换人但接得上，只影响短期，不是 direct dead；
- 不要把“跌一天/跌 10%”直接叫 dead。

只输出 JSON：
{{"theme":"...","date":"...","state":"alive|warning|dead|rotated|unknown","confidence":0.0,
"lower_high_count":0,"volume_distribution":true,"rebound_failed":null,
"evidence":["..."],"action_permissions":{{"second_catchup":false,"high_cut_low":false,"hold_second":false}}}}

=== AS-OF FACTS ===
{facts}

=== END ===
"""

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--cases", default="光伏:20260918,AI/算力/科技:20260625,传媒/游戏:20260828,电力/公用:20260918")
    a=ap.parse_args(); out=[]
    for item in a.cases.split(","):
        theme,date=item.rsplit(":",1); f=facts(theme,date)
        if not f: print("skip",item); continue
        ai_facts = {k:v for k,v in f.items() if k != "mechanical_state"}
        prompt=PROMPT.format(facts=json.dumps(ai_facts,ensure_ascii=False,indent=1))
        try:
            sid = f"asof_{theme}_{date}_{datetime.now().strftime('%H%M%S')}"
            r=requests.post(CHAT, json={"message":prompt,"session_id":sid}, timeout=240)
            r.raise_for_status(); raw=str((r.json() or {}).get("reply") or "").strip()
            txt=re.sub(r"^```(?:json)?\s*","",raw); txt=re.sub(r"\s*```\s*$","",txt)
            ans=json.loads(txt)
        except Exception as e:
            ans={"error":str(e)[:200]}
        ans.update({"theme":theme,"date":date,"segment":f["segment"],"mechanical_state":f["mechanical_state"],
                    "leader":f["leader"],"leader_name":f.get("leader_name"),"last_close":f["last_close"],
                    "lower_high_days":f["lower_high_days"],"vol_ratio5_20":f["vol_ratio5_20"]})
        out.append(ans)
        print(json.dumps(ans,ensure_ascii=False,indent=1))
    df=pd.DataFrame(out); df.to_csv(os.path.join(OUT,"ai_asof_cases.csv"),index=False,encoding="utf-8-sig")
    json.dump(out,open(os.path.join(OUT,"ai_asof_cases.json"),"w"),ensure_ascii=False,indent=1)
    print(json.dumps({"n":len(out)},ensure_ascii=False))

if __name__=="__main__":
    main()
