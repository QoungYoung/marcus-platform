# -*- coding: utf-8 -*-
"""
low_logic_agent.py — 低位看逻辑(AI判定)。对 position_class 判为 LOW 的概念，用 dsh agent 判"题材逻辑/景气/催化"，输出 logic_score(0-1)+判定。
输入: data/position_class_result.json(LOW概念) + main_line_state + wolf_theme_features + latest_hot_sectors + 位置/资金特征
输出: data/low_logic.json = {name:{logic_score,verdict,reason}}
复用 wave_agent 的 dsh /chat 链路。运行于 worker 容器。
"""
import os, sys, json, time, requests
sys.path.insert(0,"/app")
import pandas as pd
CHAT_URL=os.getenv("WAVE_CHAT_URL","http://marcus-dsh:3001/chat")
DATA=os.environ.get("DATA_DIR","/app/data")
RES=os.path.join(DATA,"position_class_result.json"); OUT=os.path.join(DATA,"low_logic.json")
ML=os.path.join(DATA,"main_line_state.json"); THEME=os.path.join(DATA,"wolf_theme_features_v5.csv"); HOT=os.path.join(DATA,"latest_hot_sectors.json")

def ld(p, dflt=None):
    try: return json.load(open(p,encoding="utf-8"))
    except Exception: return dflt
def call_agent(prompt, session="lowlogic_"):
    try:
        r=requests.post(CHAT_URL, json={"message":prompt,"session_id":session+str(int(time.time()))}, headers={"Content-Type":"application/json"}, timeout=180, verify=False)
        r.raise_for_status(); return r.json().get("reply","")
    except Exception as e: return "ERR:"+str(e)[:120]

def main():
    res=ld(RES,{})
    lows=[v for v in res.values() if isinstance(v,dict) and v.get("position")=="LOW" and "features" in v]
    if not lows:
        print("无 LOW 概念, 跳过"); return
    ml=ld(ML,{}) or {}; cat={}; hot=[]
    try:
        df=pd.read_csv(THEME)
        for _,r in df.iterrows(): cat[str(r.get("theme") or r.get("name") or "")]=float(r.get("catalyst",0) or 0)
    except Exception: pass
    h=ld(HOT,{}) or {}
    hot=h.get("hot_concepts",[]) if isinstance(h,dict) else []
    # 批量 prompt
    rows=[]
    for v in lows[:40]:
        fe=v.get("features",{}); fs=v.get("fund_flow",{})
        rows.append(f"- {v['name']}: 距1年高={fe.get('vs_1y_high_pct')}% 箱体={fe.get('box_pos_pct')}% r20={fe.get('r20')}% 主力净流入={fs.get('strength')}亿 连续={fs.get('conv')}天")
    prompt=("你是狼大'低位看逻辑'评判 agent。下面列出若干个被判定为【低位(超跌)】的东财概念，请判断每个的【题材逻辑/景气/催化】质量(是否值得埋伏低吸)，只评逻辑、不评点位/不判买卖。\n"
            "背景：主线= "+str(ml.get("main_line"))+" (catalyst: "+str(ml.get("catalyst"))+")\n"
            "主题催化： "+str(cat)+"\n"
            "热点概念： "+str(hot)+"\n"
            "【LOW概念列表】\n"+"\n".join(rows)+"\n"
            "对每个概念仅输出 JSON 对象列表(不要markdown)，每个含 {name, logic_score(0-1), verdict: 值得埋伏低吸/需等确认/逻辑弱不碰, reason(一句)}：\n"
            '{"concepts":[{"name":"","logic_score":0.0,"verdict":"","reason":""}]}')
    reply=call_agent(prompt)
    print("REPLY len", len(reply))
    # parse
    parsed=None
    try: parsed=json.loads(reply.strip().lstrip(chr(96)*3).rstrip(chr(96)).strip())
    except Exception:
        import re; m=re.search(r'\{.*\}', reply, re.S)
        if m:
            try: parsed=json.loads(m.group(0))
            except Exception: pass
    if not parsed or "concepts" not in parsed:
        print("parse_failed, raw:", reply[:200]); return
    out={}
    for c in parsed["concepts"]:
        if isinstance(c,dict) and c.get("name"): out[c["name"]]={"logic_score":float(c.get("logic_score",0) or 0),"verdict":c.get("verdict",""),"reason":c.get("reason","")}
    json.dump(out, open(OUT,"w",encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE", OUT, "concepts", len(out))
    for n,v in list(out.items())[:10]: print(" ", n, v["verdict"], "score", v["logic_score"])
if __name__=="__main__": main()
