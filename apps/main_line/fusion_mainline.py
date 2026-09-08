# -*- coding: utf-8 -*-
"""主线三信号融合评估: main_line_score = w1*研报catalyst + w2*资金持续性 + w3*相对强度
数据: data/main_line_state_<date>.json(研报catalyst, 22日期重放) + data/concept_hist.json(资金/强度)
      + data/wolf_labels_v2.json(主线标注)
用法: python apps/main_line/fusion_mainline.py [--grid]  → 网格搜索权重 + 最优 match rate
"""
import json, os, sys
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DATA = os.environ.get("DATA_DIR", "data")
# 标注主题 → 主线判定主题(THEMES 键)
MAIN_THEME_OF = {
  "AI": "AI/算力/科技", "AI硬": "AI/算力/科技", "AI应用": "AI/算力/科技",
  "科技": "AI/算力/科技", "光": "AI/算力/科技", "算力": "AI/算力/科技",
  "半导体": "半导体/芯片", "电池": "新能源/电池", "金融": "金融",
  "消费": "消费/内需", "医药": "医药", "航天": "军工/航天", "银行": "银行",
  "游戏": None, "软件": None, "数据": None, "PCB": None, "液冷": None, "稀土": None, "有色": None,
}
MAIN_THEMES = ["AI/算力/科技","半导体/芯片","新能源/电池","军工/航天","资源/周期","金融","消费/内需","医药","稳增长/基建","银行",
              "机器人/智能制造","汽车/智驾","传媒/游戏","电力/公用","农业"]
# 主线主题 → 东财概念名(子串)
THEME_CONCEPTS = {
 "AI/算力/科技": ["人工智能","算力概念","AIGC概念","AI应用","AI智能体","AI语料","DeepSeek概念","ChatGPT概念","Kimi概念","智谱AI","多模态AI","AI眼镜","光通信模块","CPO概念","液冷概念","PCB","东数西算","数据中心","云计算","边缘计算","存储芯片","AI芯片"],
 "半导体/芯片": ["半导体概念","国产芯片","存储芯片","光刻机(胶)","光刻胶","Chiplet概念","第三代半导体","第四代半导体","汽车芯片","AI芯片","光刻机","半导体材料","半导体设备","数字芯片设计","模拟芯片设计","集成电路制造","集成电路封测"],
 "新能源/电池": ["固态电池","锂电池","储能","电池技术","新能源","新能源车","电池化学品","锂电专用设备"],
 "军工/航天": ["商业航天","卫星互联网","航天航空","低空经济","无人机","军工","军工电子Ⅱ","航天装备Ⅱ","航空装备Ⅱ"],
 "资源/周期": ["黄金概念","稀土永磁","锂矿概念","工业金属","稀土","能源金属","贵金属","钨","钼","铅锌","铜","铝","锂","黄金"],
 "金融": ["券商概念","参股券商","参股银行","券商金股","保险","垂直应用软件","数字货币","证券Ⅱ","软件开发","银行"],
 "消费/内需": ["白酒","免税概念","新消费","新零售","零售概念","旅游概念","消费电子概念","文娱消费"],
 "医药": ["创新药","中药概念","CRO","减肥药","医疗器械概念","生物疫苗","精准医疗","互联医疗","AI制药（医疗）","中药Ⅱ","体外诊断","化学制剂","医疗研发外包","医疗耗材","医疗设备","生物制品"],
 "稳增长/基建": ["一带一路","中字头","铁路基建","国际工程","基建市政工程","工程建设","水泥制造","涂料","玻璃玻纤","装修建材","防水材料"],
 "机器人/智能制造": ["机器人概念","人形机器人","机器人执行器","虚拟机器人","工业母机","3D打印","工程机械概念","其他自动化设备","工控设备","机床工具","激光设备"],
 "汽车/智驾": ["汽车整车","华为汽车","小米汽车","无人驾驶","车联网(车路云)","汽车一体化压铸","汽车热管理","飞行汽车(eVTOL)","底盘与发动机系统","毫米波概念","汽车电子电气系统","汽车零部件","激光雷达"],
 "传媒/游戏": ["网络游戏","短剧互动游戏","影视概念","在线教育","职业教育","体育产业"],
 "电力/公用": ["核能核电","特高压","绿色电力","节能环保","雅下水电概念","光伏发电","核力发电","水力发电","火力发电","电网自动化设备","综合电力设备商","风力发电"],
 "农业": ["农业种植","水产养殖","农药兽药","粮食概念","生态农业","乳业","乡村振兴","渔业","生猪养殖","种子","粮食种植","肉鸡养殖","转基因","饲料"],
}
def load(p):
    return json.load(open(os.path.join(DATA, p), encoding="utf-8"))

def close_series(a):
    idx = pd.to_datetime(a["dates"])
    s = pd.Series(a["close"], index=idx).apply(pd.to_numeric, errors="coerce").dropna()
    return s.astype(float)
def net_series(a):
    idx = pd.to_datetime(a["dates"])
    s = pd.Series(a["net_amount"], index=idx).apply(pd.to_numeric, errors="coerce")
    return s.apply(lambda x: None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x))

REPORT_COUNTS = {}
try:
    REPORT_COUNTS = load("replay_report_counts.json")
except Exception:
    pass
CONCENTRATION = {}
try:
    CONCENTRATION = load("replay_concentration.json")
except Exception:
    pass
BANK_DATA = {}
try:
    BANK_DATA = load("replay_bank_industry.json")
except Exception:
    pass

def norm_catalyst(c, n):
    """研报量归一化(试验后弃用): 研报多≠错(半导体研报多但狼大也认科技主线), 归一化反而引入新偏置 → 系数0=不归一化"""
    if n <= 0 or True: return c   # 2026-09-01: 归一化试验 63%→59% 变差, 弃用
    return c / (1 + 0.3 * (n ** 0.5))

def bank_signals(dt):
    """银行行业信号(申万/东财行业, 补充概念体系缺失): fund=净流入排名百分位, conc=净流入占全体行业比例"""
    rec = BANK_DATA.get(dt)
    if not rec or not rec.get("all_net"): return None
    bk = next((b for b in rec.get("banks", []) if b.get("ts_code") == "BK0475.DC"), None)
    if not bk or bk.get("net_amount") is None: return None
    na = float(bk["net_amount"])
    all_na = [float(n) for _, n in rec["all_net"]]
    if not all_na: return None
    rank = sum(1 for n in all_na if n < na)
    fund_p = (rank + 1) / len(all_na)
    pct = bk.get("pct")
    try: pct = float(pct) if pct is not None else 0.0
    except Exception: pct = 0.0
    # 银行主升硬条件: 净流入全市场行业第一(东财rank==1 或 计算排名>=0.99) 且 涨幅>=1% (主升日)
    # 护盘日(6/5 rank9、8/12 rank39): 银行净流入强但非第一 → 不给主线分
    rank_f = bk.get("rank")
    try: rank_f = int(rank_f) if rank_f is not None else None
    except Exception: rank_f = None
    is_top = (rank_f == 1) or (fund_p >= 0.99)
    if is_top and pct >= 1.0:
        fund = 1.0
        rel = min(1.0, pct / 2.0)
    else:
        fund = min(fund_p, 0.3)   # 护盘: 资金分压低
        rel = 0.1 if pct < 1.0 else 0.3
    return {"catalyst": 0.0, "fund": fund, "rel": rel, "conc": fund}

def theme_conc(hist, dt):
    """资金集中度(替代成交集中度, concept_hist 全覆盖不依赖接口):
    Σ主题概念当日 net_amount 绝对值 / Σ全体概念 net_amount 绝对值, 9主题间百分位。
    注: 成交额反推(net/rate)受 promax 接口间歇限流影响(2025-10-27/11-04/12-11 返回空), 改用净流入集中度。"""
    t = pd.Timestamp(dt)
    all_net = {}
    for code, a in hist.items():
        s = net_series(a)[lambda x: x.index <= t].dropna()
        if not len(s): continue
        all_net[code] = abs(float(s.iloc[-1]))
    if not all_net: return {k: 0.5 for k in MAIN_THEMES}
    tot = sum(all_net.values())
    if tot <= 0: return {k: 0.5 for k in MAIN_THEMES}
    theme_amt = {}
    for th, names in THEME_CONCEPTS.items():
        codes = [c for c, a in hist.items() if a["name"] in names]
        theme_amt[th] = sum(all_net.get(c, 0) for c in codes)
    nb = [k for k in MAIN_THEMES if k != "银行"]
    share = {k: theme_amt[k] / tot for k in nb}
    order = np.array([share[k] for k in nb]).argsort().argsort()
    return {k: (order[i] + 1) / len(nb) for i, k in enumerate(nb)}

def theme_signals(hist, dt, state):
    """返回 {theme: {"catalyst","fund","rel","conc"}} 全部 0-1"""
    out = {}
    t = pd.Timestamp(dt)
    # 主题概念资金(5日累计) 与 r20
    fund = {}; rel = {}
    for th, names in THEME_CONCEPTS.items():
        codes = [c for c, a in hist.items() if a["name"] in names]
        fs = []
        for c in codes:
            net = net_series(hist[c])
            s = net[net.index <= t].dropna()
            if len(s) >= 2: fs.append(float(s.iloc[-5:].sum()) if len(s) >= 5 else float(s.sum()))
        fund[th] = float(np.mean(fs)) if fs else 0.0
        rs = []
        for c in codes:
            ser = close_series(hist[c]); base = ser[ser.index <= t]
            if len(base) >= 21 and float(base.iloc[-21]) > 0:
                rs.append(float(base.iloc[-1]) / float(base.iloc[-21]) - 1)
        rel[th] = float(np.mean(rs)) if rs else 0.0
    # 9 个概念主题间排名百分位(0-1); 银行用独立行业信号不参与排名
    NON_BANK = [th for th in MAIN_THEMES if th != "银行"]
    def pct(d):
        arr = np.array([d.get(k, 0.5) for k in NON_BANK])
        order = arr.argsort().argsort()
        return {k: (order[i] + 1) / len(arr) for i, k in enumerate(NON_BANK)}
    fund_p = pct(fund); rel_p = pct(rel)
    cat = state.get("catalyst") or {}
    counts = REPORT_COUNTS.get(dt, {})
    conc_p = theme_conc(hist, dt)   # 资金集中度(net_amount占比, 9概念主题间百分位)
    for th in MAIN_THEMES:
        if th == "银行":   # 银行=行业数据(概念体系无银行), 独立信号
            b = bank_signals(dt)
            out[th] = b if b else {"catalyst": 0.0, "fund": 0.5, "rel": 0.5, "conc": 0.5}
            continue
        c = float(cat.get(th) or 0)
        n = int(counts.get(th, 0) or 0)
        out[th] = {"catalyst": norm_catalyst(c, n), "fund": fund_p.get(th, 0.5),
                   "rel": rel_p.get(th, 0.5), "conc": conc_p.get(th, 0.5)}
    return out

def load_labels():
    v = load("wolf_labels_v2.json")
    rows = []
    for l in v.get("mainline", []):
        mt = MAIN_THEME_OF.get(l["theme"])
        if mt is None: continue
        sf = os.path.join(DATA, f"main_line_state_{l['date']}.json")
        if not os.path.exists(sf): continue
        rows.append({"date": l["date"], "theme": mt, "expect": bool(l.get("expect")), "label_theme": l["theme"]})
    return rows

_SIG_CACHE = {}
def get_signals(hist, date):
    """按日期缓存主题信号(避免网格搜索重复计算)"""
    if date not in _SIG_CACHE:
        state = load(f"main_line_state_{date}.json")
        _SIG_CACHE[date] = theme_signals(hist, date, state)
    return _SIG_CACHE[date]

def score_and_match(rows, hist, w):
    w1, w2, w3, w4 = w
    results = []
    for r in rows:
        sig = get_signals(hist, r["date"])
        sc = {th: w1 * v["catalyst"] + w2 * v["fund"] + w3 * v["rel"] + w4 * v["conc"] for th, v in sig.items()}
        rank = sorted(MAIN_THEMES, key=lambda k: -sc[k])
        pos = rank.index(r["theme"]) + 1
        match = (pos <= 2) if r["expect"] else (pos >= 3)
        results.append({**r, "score": round(sc[r["theme"]], 3), "rank": pos, "top1": rank[0], "match": match})
    return results

def main():
    hist = load("concept_hist.json")
    rows = load_labels()
    print(f"可评估标注: {len(rows)} 条")
    if "--grid" in sys.argv:
        best = None
        for w1 in np.arange(0, 1.01, 0.1):
            for w2 in np.arange(0, 1.01 - w1 + 0.001, 0.1):
                for w3 in np.arange(0, 1.01 - w1 - w2 + 0.001, 0.1):
                    w4 = round(1 - w1 - w2 - w3, 1)
                    if w4 < -0.001: continue
                    results = score_and_match(rows, hist, (round(w1,1), round(w2,1), round(w3,1), w4))
                    ok = sum(1 for x in results if x["match"])
                    rate = ok / len(results)
                    if best is None or rate > best[0]:
                        best = (rate, (round(w1,1), round(w2,1), round(w3,1), w4), results)
        rate, w, results = best
        print(f"最优权重 w=(catalyst={w[0]}, fund={w[1]}, rel={w[2]}, conc={w[3]}) → {sum(1 for x in results if x['match'])}/{len(results)} = {rate*100:.0f}%")
        print()
        print("=== 单信号对比 ===")
        for name, ww in [("纯研报",(1,0,0,0)), ("纯资金",(0,1,0,0)), ("纯强度",(0,0,1,0)), ("纯集中度",(0,0,0,1)), ("等权",(0.25,0.25,0.25,0.25))]:
            r2 = score_and_match(rows, hist, ww)
            print(f"  {name}: {sum(1 for x in r2 if x['match'])}/{len(r2)} = {sum(1 for x in r2 if x['match'])/len(r2)*100:.0f}%")
        print()
        print("=== 最优权重逐条 ===")
        for x in results:
            m = "✓" if x["match"] else "✗"
            print(f"  [{x['date']}] {x['label_theme']:6} expect={x['expect']} {m} rank={x['rank']} score={x['score']} top1={x['top1']} | 注:{x.get('note','')[:30]}")
    else:
        results = score_and_match(rows, hist, (0.25, 0.25, 0.25, 0.25))
        ok = sum(1 for x in results if x["match"])
        print(f"等权(0.25,0.25,0.25,0.25): {ok}/{len(results)} = {ok/len(results)*100:.0f}%")

if __name__ == "__main__":
    main()
