# -*- coding: utf-8 -*-
"""AI 主营归属裁决 demo(2026-09-08): chain_map v2 rejected 边界股 DeepSeek 语义复核"""
import sys, os, json, time, requests, urllib3
sys.path.insert(0, '/app')
urllib3.disable_warnings()
sys.path.insert(0, '/app/core')
from core.api_client import DEEPSEEK_API_KEY, DEEPSEEK_API_HOST, DEEPSEEK_MODEL
DATA = '/app/data'
CAP_PER_SEG = 6

def parse_json(content):
    content = (content or "").strip()
    if content.startswith("```"):
        content = content[3:]
        if content.startswith("json"): content = content[4:]
    if content.endswith("```"): content = content[:-3]
    i, j = content.find("{"), content.rfind("}")
    if i < 0 or j < i: raise ValueError("no json: " + content[:120])
    return json.loads(content[i:j+1])

def llm(system_prompt, user_prompt):
    url = "https://" + DEEPSEEK_API_HOST + "/v1/chat/completions"
    body = {"model": DEEPSEEK_MODEL, "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}],
        "temperature": 0.1, "max_tokens": 900}
    r = requests.post(url, headers={"Authorization": "Bearer " + DEEPSEEK_API_KEY,
                                    "Content-Type": "application/json"},
                      data=json.dumps(body, ensure_ascii=False).encode("utf-8"), timeout=90)
    content = r.json()["choices"][0]["message"]["content"]
    return parse_json(content)

def llm_retry(system_prompt, user_prompt):
    for _ in range(2):
        try:
            return llm(system_prompt, user_prompt)
        except Exception as e:
            last = str(e)
            time.sleep(1.2)
    return {"verdict": "unknown", "confidence": 0.0, "reason": "llm_err:" + last[:50], "suggested_kw": []}

def load_fina_bz(pro, ts):
    try:
        df = pro.fina_mainbz(ts_code=ts)
        if df is None or df.empty: return []
        df = df.sort_values("bz_sales", ascending=False)
        items = df[~df["bz_item"].isin(["行业", "产品", "地区"])].head(8)
        out = []
        for _, r2 in items.iterrows():
            bz = str(r2.get("bz_item") or "")[:40]
            try: s = float(r2.get("bz_sales") or 0)
            except Exception: s = 0
            out.append({"bz": bz, "sales": int(s)})
        return out
    except Exception: return []

SYSTEM = (
'你是 A股产业链成分研究员。判断公司主营是否真正属于给定产业链环节(该环节是整条产业链的细分场景)。'
'口径: 1) fina主营 bz_item 文本(带销售额)是首要依据, 先看主营是什么、是否主导; '
'2) 概念成分归属只是线索不可当依据(概念表收录脏, 常有跨界巨无霸); '
'3) 只回答“是否属于该环节”, 公司属于同一条产业链的其它环节判 out; '
'4) 主营名目与环节无字面重叠不等于 out(词表可能有盲区), 要按语义判断; '
'5) 信息不足或主营过杂无法确定判 unknown, 不要硬猜。'
'输出严格 JSON(仅对象, 不要代码围栏): {"verdict": "in_segment"|"out"|"unknown", "confidence": 0~1, '
'"reason": "一句话依据(<=40字)", "suggested_kw": [若 in_segment 给出 1-3 个能代表主营的环节词, 否则空数组]}')

def main():
    from app.api.market import _get_tushare_pro
    pro = _get_tushare_pro()
    cm = json.load(open(os.path.join(DATA, "chain_map_20260908.json"), encoding="utf-8"))
    rows, stats = [], {"in_segment": 0, "out": 0, "unknown": 0}
    for th in cm["themes"]:
        for seg in th["segments"]:
            rej = [r for r in seg.get("rejected", []) if r.get("ts")][:CAP_PER_SEG]
            if not rej: continue
            for r0 in rej:
                ts = r0["ts"]
                bz = load_fina_bz(pro, ts)
                user = json.dumps({
                    "theme": th["theme"], "segment": seg["label"], "role": seg.get("role"),
                    "环节概念": seg.get("concepts", []),
                    "company": {"ts": ts, "name": r0.get("name"), "mv_wan": r0.get("mv"),
                                "fina主营top(销售额万元)": bz if bz else r0.get("mainbz")},
                    "规则初筛": "rejected: 主营文本未命中环节关键词"
                }, ensure_ascii=False)
                a = llm_retry(SYSTEM, user)
                v = a.get("verdict", "unknown"); stats[v] = stats.get(v, 0) + 1
                rows.append({"theme": th["theme"], "seg": seg["label"], "ts": ts, "name": r0.get("name"),
                             "mv_wan": r0.get("mv"), "mainbz_top": (bz[:2] if bz else (r0.get("mainbz") or [])[:2]),
                             "rule": "rejected", "ai": v, "confidence": a.get("confidence", 0),
                             "reason": a.get("reason", ""), "kw": a.get("suggested_kw", [])})
                nm = str(r0.get("name") or ""); cd = str(ts).split(".")[0]
                mvs = r0.get("mv"); mvs = round(mvs) if mvs else ""
                print("[" + th["theme"] + "|" + seg["label"] + "] " + nm + "(" + cd + ") mv=" + str(mvs) + " -> AI " + v
                      + " conf=" + str(a.get("confidence")) + " | " + str(a.get("reason", ""))[:36]
                      + " | kw=" + str(a.get("suggested_kw", [])), flush=True)
                time.sleep(0.4)
    json.dump({"date": "20260908", "generator": "chain_map_ai_verify_demo", "stats": stats, "rows": rows},
              open(os.path.join(DATA, "chain_map_20260908_ai_verdicts.json"), "w"), ensure_ascii=False, indent=1)
    print("WROTE", os.path.join(DATA, "chain_map_20260908_ai_verdicts.json"), "stats", stats)

main()

def main_retry():
    from app.api.market import _get_tushare_pro
    pro = _get_tushare_pro()
    path = os.path.join(DATA, "chain_map_20260908_ai_verdicts.json")
    data = json.load(open(path, encoding="utf-8"))
    todo = [r for r in data["rows"] if r.get("ai") == "unknown" or (r.get("reason") or "").startswith("llm_err")]
    print("retry", len(todo), "rows", flush=True)
    stats = data["stats"]
    for r0 in todo:
        ts = r0["ts"]; bz = load_fina_bz(pro, ts)
        user = json.dumps({"theme": r0["theme"], "segment": r0["seg"], "role": "",
                           "环节概念": [], "company": {"ts": ts, "name": r0["name"], "mv_wan": r0.get("mv_wan"),
                           "fina主营top(销售额万元)": bz}, "规则初筛": "rejected"}, ensure_ascii=False)
        a = llm_retry(SYSTEM, user)
        v = a.get("verdict", "unknown")
        if v in stats: stats[v] = stats.get(v, 0) + 1
        if r0.get("ai") == "unknown" and not (r0.get("reason") or "").startswith("llm_err"):
            pass
        r0["ai"] = v; r0["confidence"] = a.get("confidence", 0)
        r0["reason"] = a.get("reason", ""); r0["kw"] = a.get("suggested_kw", [])
        print("  " + r0["name"] + "(" + ts.split(".")[0] + ") -> " + v + " conf=" + str(a.get("confidence")) + " | " + str(a.get("reason", ""))[:40], flush=True)
        time.sleep(0.5)
    json.dump({"date": data["date"], "generator": "chain_map_ai_verify_demo", "stats": stats, "rows": data["rows"]},
              open(path, "w"), ensure_ascii=False, indent=1)
    print("UPDATED stats", stats)

if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--retry":
    main_retry()
elif __name__ == "__main__":
    main()