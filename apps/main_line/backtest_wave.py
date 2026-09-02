# -*- coding: utf-8 -*-
# 狼大 wave_agent 回测：对给定日期，用现行两级 wave_agent 判定，与狼大原话编码的期望 (level/sub_level/op) 对比
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wave_agent as wa

# (date, wolf_quote, exp_level, exp_sub, exp_op)  —— exp 为按狼大原话的编码
TRUTH = [
    ("2025-02-20", "已完成2浪调整，接下来低点都是三浪起头", "d3", "3-1", "build"),
    ("2025-03-14", "收盘确认大三浪(过3400走完延申浪接大3浪)", "d3", "3-3", "build"),
    ("2025-03-27", "指数接近3395减到半仓以下，反弹高点", "d3", "3-5", "exit"),
    ("2025-04-09", "这里调整绝对没有结束，回踩再买", "d4", "4-1", "defense"),
    ("2025-05-09", "接可能接3浪2买，调整周期", "d3", "3-2", "side"),
    ("2025-10-19", "双底后反转大1/调整大2，现主升大3", "d3", "3-3", "build"),
    ("2016-03-22", "刚过第一轮主升段2850-3000，现在叫深蹲", "d3", "3-2", "side"),
    ("2016-04-09", "进入主升段，调仓换股做价差", "d3", "3-3", "build"),
    ("2016-05-18", "3097的下跌浪，最后5浪以盘代跌", "down", "5浪下跌", "defense"),
    ("2016-05-19", "大盘明显W底，转折点", "d1", "W底", "build"),
    ("2021-08-19", "现在只是第一波主跌的五浪底", "down", "5浪", "defense"),
    ("2021-11-17", "猴市已经到底，磨底等契机回主升", "d1", "", "side"),
]

def op_ok(a, e):
    # 允许 build 与 side 都算顺(可参与), defense/exit 反向; 这里按"能否建仓"粗分
    allowed = {"build"}
    if e in ("build", "side"):
        return a in ("build", "side")
    if e in ("defense", "exit"):
        return a in ("defense", "exit", "t_only")
    return True

def lvl_family_ok(a, e):
    # d3 主升家族: build 相关; d1/d2 底部/调整; down 防御
    fam = {"build": {"d1","d3"}, "side": {"d1","d2","d3"}, "defense": {"d4","d5","down"}, "exit":{"d4","d5","down"}}
    return a in fam.get(e, set())

results=[]
for date, quote, el, es, eo in TRUTH:
    f = wa.index_features(date)
    if f is None:
        results.append({"date":date,"quote":quote,"err":"no_data"})
        continue
    prompt = wa.build_prompt(f)
    try:
        reply = wa.call_agent(prompt, session="bt_")
        res = wa.parse(reply)
    except Exception as ex:
        results.append({"date":date,"quote":quote,"err":str(ex)[:120]}); continue
    res["date"]=date; res["features"]=f
    res["exp_level"]=el; res["exp_sub"]=es; res["exp_op"]=eo; res["wolf_quote"]=quote
    results.append(res)
    print(json.dumps(res, ensure_ascii=False)[:300], file=sys.stderr)

out="data/wave_backtest_result.json"
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out,"w",encoding="utf-8") as fp: json.dump(results, fp, ensure_ascii=False, indent=2)
print("WROTE", out, "n=", len(results))
