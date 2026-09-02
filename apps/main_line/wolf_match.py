# -*- coding: utf-8 -*-
"""
wolf_match.py — 狼大判断标注集 vs 模块输出 match rate
=====================================================
数据: data/wolf_labels.json(标注) + data/concept_hist.json(概念历史) 
      + data/main_line_state.json(当前主线, 备用)
方法: 每条标注(date, theme, judgment) → 解析主题到东财概念 → 按时点算 position_class
      输出(位置/资金/action/r20/资金排名) → 按判定规则算命中。
注意: main_line_judge 依赖研报且无历史数据, 无法按日期重放 → 主线用模块"确认阶段"代理:
      主线主题在当日应体现为 资金流入TOP 或 强势(r20>0) —— 这是主线判定模块的
      "候选+动量/资金转强"确认逻辑, 不是研报催化agent本身。
用法: cd 仓库根 && .venv/bin/python apps/main_line/wolf_match.py
输出: data/wolf_match_result.json + stdout
"""
import os, json, sys
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import position_class as pc

DATA = os.environ.get("DATA_DIR", "data")

# ---- 主题 → 东财概念名 (子串匹配 concept_hist 名称) ----
THEME_CONCEPTS = {
  "算力":    ["算力概念", "东数西算", "数据中心", "云计算", "边缘计算"],
  "AI":      ["人工智能", "AI应用", "AI智能体", "AI语料", "AIGC概念", "DeepSeek概念", "ChatGPT概念", "Kimi概念", "智谱AI", "多模态AI", "AI眼镜", "AI手机", "AI芯片", "AI制药（医疗）"],
  "AI硬":    ["光通信模块", "CPO概念", "液冷概念", "AI芯片", "半导体概念", "国产芯片", "存储芯片", "PCB"],
  "AI应用":  ["AI应用", "AIGC概念", "多模态AI", "AI智能体", "Kimi概念", "智谱AI", "ChatGPT概念", "DeepSeek概念"],
  "科技":    ["人工智能", "算力概念", "半导体概念", "国产芯片", "光通信模块", "CPO概念", "液冷概念", "AI应用", "PCB", "消费电子概念", "存储芯片", "AIGC概念", "东数西算", "数据中心", "边缘计算", "云计算"],
  "半导体":  ["半导体概念", "国产芯片", "存储芯片", "光刻机(胶)", "光刻胶", "Chiplet概念", "第三代半导体", "第四代半导体", "汽车芯片", "AI芯片"],
  "光":      ["光通信模块", "CPO概念"],
  "有色":    ["黄金概念", "稀土永磁", "锂矿概念"],
  "稀土":    ["稀土永磁"],
  "医药":    ["创新药", "中药概念", "CRO", "减肥药", "医疗器械概念", "生物疫苗", "精准医疗", "互联医疗", "医疗美容", "AI制药（医疗）", "创新医疗服务"],
  "电力":    ["绿色电力", "智能电网", "电网概念", "特高压", "核能核电", "虚拟电厂"],
  "航天":    ["商业航天", "卫星互联网", "航天航空", "低空经济", "无人机", "军工"],
  "机器人":  ["机器人概念", "人形机器人", "减速器", "机器人执行器", "虚拟机器人", "机器视觉"],
  "电池":    ["固态电池", "锂电池", "储能", "电池技术"],
  "金融":    ["券商概念", "参股券商", "参股银行", "券商金股"],
  "PCB":     ["PCB", "消费电子概念", "苹果概念"],
  "液冷":    ["液冷概念"],
  "软件":    ["信创", "国产软件", "鸿蒙概念"],
  "数据":    ["大数据", "数据中心", "数据安全", "数据确权", "数据要素", "时空大数据"],
  "消费":    ["白酒", "免税概念", "新消费", "新零售", "零售概念", "旅游概念", "消费电子概念", "文娱消费"],
  "游戏":    ["影视概念", "短剧互动游戏", "网络游戏"],
}

def load(p):
    return json.load(open(os.path.join(DATA, p), encoding="utf-8"))

def close_series(a):
    idx = pd.to_datetime(a["dates"]); s = pd.Series(a["close"], index=idx).dropna(); return s.astype(float)

def net_series(a):
    idx = pd.to_datetime(a["dates"]); s = pd.Series(a["net_amount"], index=idx)
    return s.apply(lambda x: None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x))

def fund_at(net, upto):
    s = net[net.index <= pd.Timestamp(upto)].dropna()
    if not len(s): return "flat"
    last = float(s.iloc[-1]); conv = 0
    for x in reversed(s.values):
        if (x > 0) == (last > 0) and x != 0: conv += 1
        else: break
    return {"dir": "in" if last > 0 else ("out" if last < 0 else "flat"), "conv": conv}

def fwd_ret(ser, upto, n):
    base = ser[ser.index <= pd.Timestamp(upto)]; fut = ser[ser.index > pd.Timestamp(upto)]
    if len(base) < 1 or len(fut) < n or base.isna().any() or fut.isna().any(): return None
    b0 = float(base.iloc[-1]);
    if b0 <= 0: return None
    return (float(fut.iloc[n-1]) / b0 - 1) * 100

def concept_at(hist, code, dt, rel_map=None):
    a = hist[code]
    ser = close_series(a); base = ser[ser.index <= pd.Timestamp(dt)]
    if len(base) < 60 or float(base.min()) <= 0 or base.isna().any(): return None
    f = pc.position_features(base)
    if f is None: return None
    f["structure"] = pc.structure_of(base)   # G1 结构形态(与 position_class 生产一致)
    rel = (rel_map or {}).get(code)          # G2 相对主线位置
    if rel: f["rel_mainline"] = rel["level"]; f["rel_mainline_pct"] = rel["pct"]
    f["confirm_stage"] = pc.confirm_chain(base, net_series(hist[code])).get("stage", "下跌中")  # P1 确认链
    f["index_confirm"] = pc.read_index_confirm()  # 指数确认链宏观gate
    cls = pc.classify(f)
    fs = fund_at(net_series(a), dt)
    a_cur, _ = pc.resonance(cls["position"], f, fs, None, None, "t_only", {})
    a_build, _ = pc.resonance(cls["position"], f, fs, None, None, "build", {})
    st = (f.get("structure") or {}).get("desc", "flat")
    relv = f.get("rel_mainline", "mid")
    return {"position": cls["position"], "fund": fs["dir"], "action_cur": a_cur,
            "action_build": a_build, "r20": fwd_ret(ser, dt, 20), "structure": st, "rel_mainline": relv}

def build_net_rank(hist, dt):
    """dt 当日所有概念资金排名百分位(1=流入最多)"""
    vals = {}
    for code, a in hist.items():
        s = net_series(a)[lambda x: x.index <= pd.Timestamp(dt)].dropna()
        if not len(s): continue
        vals[code] = float(s.iloc[-1])
    if not vals: return {}
    arr = np.array(list(vals.values()))
    order = arr.argsort().argsort()          # 0=最小(流出最多)
    pct = {c: (order[i] + 1) / len(arr) for i, c in enumerate(vals)}
    return pct

def main():
    labels_file = sys.argv[1] if len(sys.argv) > 1 else "wolf_labels.json"
    labels = load(labels_file)
    hist = load("concept_hist.json")
    # 主题 → codes
    name2code = {}
    for code, a in hist.items(): name2code.setdefault(a["name"], code)
    theme_codes = {}
    for th, names in THEME_CONCEPTS.items():
        codes = [name2code[n] for n in names if n in name2code]
        theme_codes[th] = codes
    print("主题概念映射:", {k: len(v) for k, v in theme_codes.items()})

    dates = sorted({l["date"] for sec in labels.values() if isinstance(sec, list) for l in sec})
    rank_cache = {dt: build_net_rank(hist, dt) for dt in dates}
    rel_cache = {dt: pc.build_rel_map(hist, upto=dt) for dt in dates}   # G2 相对主线位置(按时点)

    results = []
    def agg_rows(rows):
        """rows: list of dict per concept"""
        if not rows: return None
        n = len(rows)
        pos = {"HIGH": 0, "MID": 0, "LOW": 0}
        for r in rows: pos[r["position"]] = pos.get(r["position"], 0) + 1
        fund_in = sum(1 for r in rows if r["fund"] == "in")
        t_only = sum(1 for r in rows if "减仓" in r["action_cur"] or "只做T" in r["action_cur"])
        buy = sum(1 for r in rows if ("低吸" in r["action_cur"] or "回踩" in r["action_cur"]))
        struct_high = sum(1 for r in rows if r["structure"] in ("双头M顶", "新高回落", "双头M顶+新高回落", "新高回落+破位", "双头M顶+破位"))
        rel_low = sum(1 for r in rows if r["rel_mainline"] == "low")
        r20 = [r["r20"] for r in rows if r["r20"] is not None]
        return {"n": n, "HIGH": pos["HIGH"]/n, "MID": pos["MID"]/n, "LOW": pos["LOW"]/n,
                "fund_in": fund_in/n, "t_only": t_only/n, "buy": buy/n, "struct_high": struct_high/n,
                "rel_low": rel_low/n, "r20_mean": float(np.mean(r20)) if r20 else None}

    def eval_main(l, rows, rank):
        """主线融合口径(统一评估器, 2026-09-02): score = 0.3*fund + 0.2*rel + 0.5*conc (+银行)
        复用 fusion_mainline 的 theme_signals/bank_signals; 研报权重 0(最优网格)"""
        import fusion_mainline as fm
        mt = fm.MAIN_THEME_OF.get(l["theme"])
        if mt is None: return None, {"reason": "no_mainline_theme"}
        sf = os.path.join(DATA, f"main_line_state_{l['date']}.json")
        if not os.path.exists(sf):
            return None, {"reason": "no_state"}
        state = load(f"main_line_state_{l['date']}.json")
        sig = fm.theme_signals(hist, l["date"], {"catalyst": state.get("catalyst") or {}})
        bk = fm.bank_signals(l["date"])
        if bk: sig["银行"] = bk
        else: sig.pop("银行", None)
        sc = {th: 0.3 * v["fund"] + 0.2 * v["rel"] + 0.5 * v["conc"] for th, v in sig.items()}
        ranked = sorted(sc, key=lambda k: -sc[k])
        pos = ranked.index(mt) + 1 if mt in ranked else 99
        match = (pos <= 2) if l["expect"] else (pos >= 3)
        detail = {"rank": pos, "top1": ranked[0] if ranked else None,
                  "score": round(sc.get(mt, 0), 3),
                  "sig": {k: round(v, 3) for k, v in sig.get(mt, {}).items()}}
        return match, detail

    for sec, sec_labels in labels.items():
        if not isinstance(sec_labels, list): continue
        for l in sec_labels:
            if sec == "mainline":
                # 主线: 融合口径(fusion_mainline 信号), 不依赖概念 concept_at(60天门槛会排除早期标注)
                match, detail = eval_main(l, None, None)
                if match is None and detail is not None and detail.get("reason") in ("no_mainline_theme", "no_state"):
                    results.append({"sec": sec, **l, "match": None, "reason": detail["reason"]}); continue
                results.append({"sec": sec, **l, "match": match, "detail": detail}); continue
            codes = theme_codes.get(l["theme"], [])
            if not codes:
                results.append({"sec": sec, **l, "match": None, "reason": "no_concept_mapping"}); continue
            rows = []
            rel = rel_cache.get(l["date"], {})
            for c in codes:
                r = concept_at(hist, c, l["date"], rel)
                if r: rows.append(r)
            if not rows:
                results.append({"sec": sec, **l, "match": None, "reason": "no_data"}); continue
            a = agg_rows(rows); a["codes"] = codes
            rank = rank_cache.get(l["date"], {})
            if l["judgment"] == "high":
                match = a["HIGH"] >= 0.5 or a["struct_high"] >= 0.5   # G1: 价格高位 或 结构高位(双头/M顶/新高回落)
                results.append({"sec": sec, **l, "match": match, "detail": a})
            elif l["judgment"] == "low":
                not_high = a["HIGH"] < 0.5
                rel_low = a.get("rel_low", 0) >= 0.5   # G2: 模块识别该主题为相对主线低位(250日涨幅落后)
                match = not_high or rel_low
                results.append({"sec": sec, **l, "match": match, "detail": {**a, "not_high": not_high, "rel_low_ok": rel_low}})
            elif l["judgment"] == "dip_buy_oversold":
                cand = (a["LOW"] + a["MID"]) >= 0.5 and a["fund_in"] >= 0.4
                match = cand
                results.append({"sec": sec, **l, "match": match, "detail": {**a, "cand": cand}})
            elif l["judgment"] == "dip_buy_strong":
                # 狼大在主线强势位低吸(持仓主线回踩加仓): 预期模块识别该主题为强势位(高位/上涨MID) 或 给出回踩低吸
                match = a["HIGH"] >= 0.5 or (a["MID"] >= 0.5 and (a.get("r20_mean") or 0) > 0) or a["buy"] >= 0.5
                results.append({"sec": sec, **l, "match": match, "detail": a})
            elif l["judgment"] == "dip_buy_pending":
                # 狼大预告"等下来再埋伏": 预期模块当前不给买点(观望) — 模块观望=狼大等待
                match = a["buy"] < 0.5
                results.append({"sec": sec, **l, "match": match, "detail": a})
            elif l["judgment"] == "t_only":
                match = a["t_only"] >= 0.5   # G1: 只看模块 action(减仓/只做T), 不再要求 position HIGH
                results.append({"sec": sec, **l, "match": match, "detail": a})

    # 汇总
    json.dump(results, open(os.path.join(DATA, "wolf_match_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    by_sec = {}
    for r in results:
        by_sec.setdefault(r["sec"], []).append(r)
    for sec, rs in by_sec.items():
        judged = [r for r in rs if r["match"] is not None]
        ok = sum(1 for r in judged if r["match"])
        print()
        print("==== %s: %d/%d 命中 (%.0f%%) ====" % (sec, ok, len(judged), 100*ok/len(judged) if judged else 0))
        for r in rs:
            m = "✓" if r["match"] else ("-" if r["match"] is None else "✗")
            d = r.get("detail", {})
            dstr = ""
            if r["sec"] == "mainline" and isinstance(d, dict):
                if "rank" in d:
                    dstr = "rank=%s top1=%s score=%.3f" % (d.get("rank"), d.get("top1"), d.get("score") or 0)
                else:
                    dstr = "med_rank=%.2f r20=%.1f%%" % (d.get("med_rank") or 0, d.get("r20_mean") or 0)
            elif isinstance(d, dict) and "HIGH" in d:
                dstr = "HIGH=%.0f%% MID=%.0f%% LOW=%.0f%% fund_in=%.0f%% t_only=%.0f%%" % (
                    100*d["HIGH"], 100*d["MID"], 100*d["LOW"], 100*d.get("fund_in",0), 100*d.get("t_only",0))
            print(f"  [{r['date']}] {r['theme']:6} {r['judgment']:8} {m}  {dstr}  | {r.get('note','')[:40]}")

if __name__ == "__main__":
    main()
