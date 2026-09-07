# -*- coding: utf-8 -*-
"""
rotation_universe.py — P2 主线内"细分宇宙+拥挤度"（2026-09-02）
国算/液冷/存储/材料 vs 大芯/大光(拥挤侧)；给 rotation_gate 提供实时代理:
  - mainline_sucking  : 主线资金是否困在单一方向(抽血/明牌)  top1_share>=0.65 或仅1子方向流入
  - rotation_healthy  : 轮动是否健康(≥2子方向资金流入 且 top1_share<0.65 = 有承接/接力)
  - crowded_top/room_bottom : 拥挤(相对主线高位) vs 有空间(相对低位/资金流入) 子方向
输出: data/rotation_universe_result.json
用法: python apps/main_line/rotation_universe.py   （worker 每轮可调 proxies()）
"""
import os, sys, json, collections, datetime
DATA = os.environ.get("DATA_DIR", "data")
POS = os.path.join(DATA, "position_class_result.json")
HIST = os.path.join(DATA, "concept_hist.json")

# 主线内细分宇宙（东财概念名关键词；"材料"=新材料/碳基近似, 半导体材料概念缺失待个股级）
def _norm(s):
    return str(s).replace(" ", "").replace("　", "")

SUB_UNIVERSE = {
    "科技/AI(总集)": ["人工智能", "AI应用", "AIGC", "AI智能体", "多模态", "智谱", "AIPC", "AI手机", "AI眼镜",
                    "AI语料", "AI芯片", "消费电子", "算力", "数据中心", "云计算", "大数据", "国资云", "腾讯云", "算力租赁",
                    "液冷", "存储", "半导体", "芯片", "数字芯片", "模拟芯片", "半导体材料", "半导体设备", "光刻",
                    "光通信", "CPO", "光纤", "铜缆", "电源设备", "信创", "软件", "数据要素", "数字经济"],
    "AI应用": ["AI应用", "AIGC", "AI智能体", "多模态", "AI语料", "智谱", "信创", "软件开发", "软件",
               "数字经济", "数据要素", "数据确权", "垂直应用软件", "横向通用软件", "国产软件"],
    "AI终端": ["AIPC", "AI手机", "AI眼镜", "消费电子"],
    "国算/算力": ["算力", "数据中心", "云计算", "大数据", "国资云", "腾讯云", "算力租赁"],
    "液冷": ["液冷概念", "液冷服务器"],
    "存储": ["存储芯片"],
    "材料": ["半导体材料", "光刻胶", "光刻机(胶)", "碳基材料"],
    "芯片/半导体": ["半导体概念", "半导体设备", "国产芯片", "AI芯片", "第三代半导体", "第四代半导体",
                    "数字芯片设计", "模拟芯片设计", "光刻机"],
    "光通信": ["光通信模块", "CPO", "光纤概念"],
    "铜缆/电源": ["铜缆高速连接", "电源设备"],
    "医药医疗": ["创新药", "中药", "医药", "医疗", "医疗器械", "疫苗", "减肥药", "生物医药", "CRO"],
    "电力": ["电力", "绿色电力", "特高压", "电网", "水电", "火电", "虚拟电厂"],
    "金融": ["银行", "券商", "证券", "保险", "金融科技", "互联金融"],
    "军工航天": ["军工", "航天", "国防", "卫星", "无人机", "导弹", "低空经济", "商业航天", "航空"],
    "有色贵金属": ["黄金", "贵金属", "有色", "稀土", "锂", "铜", "铝", "小金属", "钴"],
}
META_GROUPS = {"科技/AI(总集)"}
# 伞概念/AI应用产品级概念(非精细产业链子方向), 统计健康时剔除, 避免"伞概念内部微流入"误判健康
UMBRELLA_SUBS = {"人工智能", "AIGC概念", "AI应用", "AI智能体", "AI语料", "DeepSeek概念",
                 "ChatGPT概念", "Kimi概念", "智谱AI", "多模态AI", "AI眼镜"}

# ── LLM 每周分类补丁(rotation_universe_classified.json)：把 classify_rotation_universe.py 的新增概念并入对应组 ──
def _load_classified_ext():
    try:
        import os as _os
        p = _os.path.join(DATA, "rotation_universe_classified.json")
        if not _os.path.exists(p): return
        d = json.load(open(p, encoding="utf-8"))
        for it in (d.get("additions") or []):
            g = it.get("group"); nm = it.get("concept")
            if g and g in SUB_UNIVERSE and nm:
                kws = SUB_UNIVERSE[g]
                if not any(_norm(k) == _norm(nm) for k in kws):
                    kws.append(nm)
    except Exception:
        pass

_load_classified_ext()

def _themes_from_state():
    """读 main_line_state.json → [main_line]+candidates 主题列表; 失败返回 None。"""
    try:
        st = json.load(open(os.path.join(DATA, "main_line_state.json"), encoding="utf-8"))
        return [st.get("main_line")] + list(st.get("candidates") or [])
    except Exception:
        return None

def get_sub_universe():
    """动态派生主线子方向(不写死): 优先读 AI 细分缓存 rotation_sub_universe.json。
    - 缓存 main_line/candidates 与当前一致 → 直接返回缓存.subs(AI 聚成的语义子方向);
    - 否则用 THEME_CONCEPTS 每概念各自成一组(仍动态, 覆盖主线但较散);
    - 缺 main_line_state / fusion → 回退写死 SUB_UNIVERSE(仅全局兜底, 非主线路径)。"""
    try:
        from fusion_mainline import THEME_CONCEPTS
    except Exception:
        return SUB_UNIVERSE
    try:
        cache = json.load(open(os.path.join(DATA, "rotation_sub_universe.json"), encoding="utf-8"))
    except Exception:
        cache = None
    themes = _themes_from_state()
    if themes is None:
        return SUB_UNIVERSE
    main = themes[0] if themes else ""
    cands = [t for t in themes[1:] if t]
    if cache and cache.get("main_line") == main and (cache.get("candidates") or []) == cands and cache.get("subs"):
        return cache["subs"]
    sub = {}
    for th in themes:
        if not th: continue
        for c in (THEME_CONCEPTS.get(th) or []):
            if c: sub.setdefault(c, [c])
    return sub if sub else SUB_UNIVERSE

def load_json(p):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return {}

PROXY_STATE = "rotation_proxy_state.json"

def _load_state():
    try: return json.load(open(os.path.join(DATA, PROXY_STATE), encoding="utf-8"))
    except Exception: return {}

def _save_state(st):
    try:
        os.makedirs(DATA, exist_ok=True)
        json.dump(st, open(os.path.join(DATA, PROXY_STATE), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass

def _apply_hysteresis(raw_healthy, raw_sucking):
    """确认制/惯性: healthy/sucking 需连续 ROT_CONFIRM_DAYS 天稳定才翻转; 单日跳变不生效。
    状态按天(last_date)持久化, 一天只推进一次, 返回确认后的 (healthy, sucking)。"""
    CONFIRM = int(os.getenv("ROT_CONFIRM_DAYS", "3"))
    today = datetime.date.today().isoformat()
    st = _load_state()
    if st.get("last_date") == today and "healthy" in st and "sucking" in st:
        return bool(st.get("healthy")), bool(st.get("sucking"))
    raw = (bool(raw_healthy), bool(raw_sucking))
    if not st or "healthy" not in st:
        _save_state({"last_date": today, "healthy": raw[0], "sucking": raw[1], "pend_days": 0})
        return raw
    prev_h = bool(st.get("healthy")); prev_s = bool(st.get("sucking"))
    if raw[0] == prev_h and raw[1] == prev_s:
        _save_state({"last_date": today, "healthy": raw[0], "sucking": raw[1], "pend_days": 0})
        return raw
    pd = int(st.get("pend_days", 0))
    if st.get("pend_h") == raw[0] and st.get("pend_s") == raw[1]:
        pd += 1
    else:
        pd = 1
    if pd >= CONFIRM:
        _save_state({"last_date": today, "healthy": raw[0], "sucking": raw[1], "pend_days": 0})
        return raw
    _save_state({"last_date": today, "healthy": prev_h, "sucking": prev_s,
                 "pend_h": raw[0], "pend_s": raw[1], "pend_days": pd})
    return prev_h, prev_s

def match_names(names, kws):
    return [n for n in names if any(k in n for k in kws)]

def proxies(pos=None):
    """从 position_class_result 计算子方向拥挤/资金/轮动健康代理。pos=None 自动读文件。"""
    pos = pos if pos is not None else load_json(POS)
    if not pos:
        return {"mainline_sucking": None, "rotation_healthy": None, "crowded_top": [], "room_bottom": [], "detail": {}}
    names_by_code = {}
    for code, v in pos.items():
        nm = str(v.get("name") or "")
        if nm: names_by_code[code] = nm
    out_detail = {}
    SU = get_sub_universe()   # 概念级: 跟主线动态派生子方向(不截断)
    # 时间平滑: 用 concept_hist 的 N 日累计净流入替代单快照 fund_flow, 降单日噪
    NET_DAYS = int(os.getenv("ROT_NET_DAYS", "10"))
    hist = {}
    try:
        import os as _o
        hist = load_json(_o.path.join(DATA, "concept_hist.json"))
    except Exception:
        hist = {}
    net10_by_code = {}
    for code, a in hist.items():
        if not isinstance(a, dict): continue
        vals = [float(v) for v in (a.get("net_amount") or []) if v is not None]
        if vals:
            net10_by_code[code] = sum(vals[-NET_DAYS:])
    for sub, kws in SU.items():
        rows = []
        for code, nm in names_by_code.items():
            if any(_norm(k) in _norm(nm) for k in kws):
                v = pos[code]
                fe = v.get("features") or {}
                fund = v.get("fund_flow") or {}
                net = net10_by_code.get(code, fund.get("strength") or 0) if net10_by_code else (fund.get("strength") or 0)
                fdir = ("in" if net > 0 else ("out" if net < 0 else fund.get("dir")))
                rows.append({"name": nm, "position": v.get("position"), "action": v.get("action"),
                             "rel": fe.get("rel_mainline"), "box": fe.get("box_pos_pct"),
                             "vs1y": fe.get("vs_1y_high_pct"), "fund": fdir,
                             "net": net})
        if not rows:
            out_detail[sub] = {"n": 0}; continue
        net = sum(r["net"] for r in rows if (r["fund"] == "in"))
        net_out = -sum(r["net"] for r in rows if (r["fund"] == "out"))
        rel_hi = sum(1 for r in rows if r["rel"] == "high")
        rel_lo = sum(1 for r in rows if r["rel"] == "low")
        lowmid = sum(1 for r in rows if r["position"] in ("LOW", "MID"))
        vs = [r["vs1y"] for r in rows if isinstance(r.get("vs1y"), (int, float))]
        out_detail[sub] = {"n": len(rows), "net_in_亿": round(net, 1), "net_out_亿": round(net_out, 1),
                           "rel_high_n": rel_hi, "rel_low_n": rel_lo, "lowmid_n": lowmid,
                           "avg_vs1y": round(sum(vs) / len(vs), 1) if vs else None,
                           "crowd": round(rel_hi / max(len(rows), 1), 2)}
    subs = list(SU.keys())
    def netx(s):
        d = out_detail.get(s) or {}
        return (d.get("net_in_亿") or 0) - (d.get("net_out_亿") or 0)
    nets = {s: netx(s) for s in subs}
    # 健康口径: "≥2 个显著净流入子方向" + 分布<0.8, 剔除伞概念
    # 显著阈值用 data-backed: SIG_FRAC × 当日最大|net|(相对量, 反过拟合), 下限 ROT_NET_MIN(默认0)
    SIG_FRAC = float(os.getenv("ROT_SIG_FRAC", "0.1"))
    NET_MIN = float(os.getenv("ROT_NET_MIN", "0"))
    fine = [s for s in subs if s not in UMBRELLA_SUBS] or list(subs)
    _max_abs = max([abs(nets.get(s, 0)) for s in fine] or [0])
    th = max(SIG_FRAC * _max_abs, NET_MIN)
    sig_subs = [s for s in fine if abs(nets.get(s, 0)) >= th] or fine
    sig_in = [s for s in sig_subs if nets[s] > 0]
    abs_sum = sum(abs(v) for v in nets.values()) or 1
    top_s = max(sig_subs, key=lambda s: abs(nets[s]))
    _sig_abs = sum(abs(nets[s]) for s in sig_subs) or 1
    top1_share = abs(nets[top_s]) / _sig_abs if sig_subs else 0.0
    if sig_in:
        _in_sum = sum(nets[s] for s in sig_in)
        _top_in = max(nets[s] for s in sig_in)
        top_in_share = _top_in / _in_sum if _in_sum else 0.0
    else:
        top_in_share = 0.0
    in_subs = [s for s in subs if nets[s] > 0]   # 记账用(全量)
    healthy = bool(len(sig_in) >= 2 and top_in_share < 0.8)
    sucking = bool(len(sig_in) <= 1 or (sig_in and top_in_share >= 0.8))
    # 确认制: 连续 ROT_CONFIRM_DAYS 天稳定才翻转, 防单日跳变
    healthy, sucking = _apply_hysteresis(healthy, sucking)
    # 真实公募拥挤(rotation_crowding.json, build_crowding 产出) 与 rel 语义融合
    crowd_real = None
    try:
        cf = json.load(open(os.path.join(DATA, "rotation_crowding.json"), encoding="utf-8"))
        u = cf.get("universe") or {}
        crowd_real = {k: {"avg_float": (v.get("avg_float_per_held") or 0), "ties": v.get("n_funds_ties") or 0}
                      for k, v in u.items()}
    except Exception:
        pass
    if crowd_real:
        for s in subs:
            r = crowd_real.get(s) or {}
            (out_detail.setdefault(s, {}))["crowd_real_avg_float"] = round(r.get("avg_float") or 0, 4)
    # 双维打分: 拥挤度(真实基金 avg_float 归一化) × 位置空间(距高点折让/rel低位/低中位占比)
    maxc = max([((crowd_real or {}).get(s) or {}).get("avg_float") or 0 for s in subs] or [0]) or 1
    scores = {}
    for s in subs:
        d = out_detail.get(s) or {}
        n = max(d.get("n") or 1, 1)
        rel_lo = (d.get("rel_low_n") or 0) / n
        lowmid = (d.get("lowmid_n") or 0) / n
        av = d.get("avg_vs1y")
        dist = max(0.0, min(1.0, (-(av if isinstance(av, (int, float)) else 0)) / 30.0))
        space = round(min(1.0, 0.45 * dist + 0.35 * rel_lo + 0.20 * lowmid), 2)
        crowd = round(min(1.0, ((crowd_real.get(s) or {}).get("avg_float") or 0) / maxc), 2)
        scores[s] = {"crowd_score": crowd, "space_score": space}
    def pick(pred, key):
        arr = [s for s in subs if s not in META_GROUPS and pred(scores[s])]
        arr.sort(key=lambda s: key(s), reverse=True)
        return arr
    crowded   = pick(lambda sc: sc["crowd_score"] >= 0.55 and sc["space_score"] < 0.55, lambda s: scores[s]["crowd_score"])[:3]
    holdT     = pick(lambda sc: sc["crowd_score"] >= 0.55 and sc["space_score"] >= 0.55, lambda s: scores[s]["space_score"])[:3]
    room_cand = pick(lambda sc: sc["crowd_score"] < 0.55 and sc["space_score"] >= 0.55, lambda s: scores[s]["space_score"])[:3]
    crowded_represent = []
    try:
        cf = json.load(open(os.path.join(DATA, "rotation_crowding.json"), encoding="utf-8"))
        for key in ("chip_top", "optics_top"):
            for it in (cf.get(key) or [])[:2]:
                nm = it.get("name") or it.get("symbol")
                crowded_represent.append("%s(%s)" % (nm, it.get("symbol")))
    except Exception:
        pass
    return {"mainline_sucking": sucking, "rotation_healthy": healthy,
            "top1_sub": top_s, "top1_share": round(top1_share, 2), "inflow_subs": in_subs,
            "crowded_top": crowded, "holdT_top": holdT, "room_bottom": room_cand, "detail": out_detail,
            "sub_scoring": scores, "crowding_real": bool(crowd_real), "crowded_represent": crowded_represent}

def build_crowding_blacklist(n_funds_min=4, float_pct_min=1.0):
    """拥挤无空间子方向的成分股黑名单 → data/crowding_blacklist.json（个股级, 2026-09-03 v2）
    旧: 拥挤子方向整概念成分全拦(误拦 E10/E11/E12 等轻仓/未持仓股)
    新: 仅拦"公募核心拥挤"个股: n_funds>=n_funds_min 且 sum_float>=float_pct_min%
        (rotation_crowding.stock 个股级PIT口径; E06-E13回测确认N4/F1.0最优)
    兼容字段: symbols(旧整列表) + symbols_detail(per-symbol reason)"""
    import json as _json
    p = _json.load(open(os.path.join(DATA, "rotation_crowding.json"), encoding="utf-8")) if os.path.exists(os.path.join(DATA, "rotation_crowding.json")) else {}
    crowd_stock = p.get("stock") or {}
    try:
        pr = proxies()
        crowd_subs = pr.get("crowded_top") or []
    except Exception:
        crowd_subs = []
    concepts = []
    for sub in crowd_subs:
        cu = (p.get("universe") or {}).get(sub) or {}
        concepts += [c for c in (cu.get("concepts") or [])]
    members = []
    if concepts:
        try:
            import psycopg2
            conn = psycopg2.connect(os.environ.get("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name = ANY(%s)", (concepts,))
            members = [r[0] for r in cur.fetchall()]; cur.close(); conn.close()
        except Exception:
            members = []
    symbols = []
    symbols_detail = {}
    for sym in members:
        s = crowd_stock.get(sym) or {}
        nf = int(s.get("n_funds") or 0)
        fl = float(s.get("sum_float") or 0)
        if nf >= n_funds_min and fl >= float_pct_min:
            symbols.append(sym)
            symbols_detail[sym] = {"symbol": sym,
                                   "subs": crowd_subs,
                                   "n_funds": nf,
                                   "float_pct": round(fl, 3),
                                   "sum_mkv_yi": round(float(s.get("sum_mkv") or 0) / 1e8, 1),
                                   "rule": {"n_funds_min": n_funds_min, "float_pct_min": float_pct_min},
                                   "reason": "公募核心拥挤: n_funds=%d, sum_float=%.2f%%" % (nf, fl)}
    out = {"subs": crowd_subs, "concepts": concepts, "symbols": symbols,
           "symbols_detail": symbols_detail, "rule": {"n_funds_min": n_funds_min, "float_pct_min": float_pct_min},
           "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    json.dump(out, open(os.path.join(DATA, "crowding_blacklist.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("crowding_blacklist v2 subs=", crowd_subs, "concepts=", len(concepts),
          "members=", len(members), "blocked=", len(symbols), "rule=N%d/F%.1f" % (n_funds_min, float_pct_min))
    return out

def main():
    out = proxies()
    print(json.dumps(out, ensure_ascii=False, indent=1)[:4000])
    try:
        json.dump(out, open(os.path.join(DATA, "rotation_universe_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("WROTE", os.path.join(DATA, "rotation_universe_result.json"))
        build_crowding_blacklist()
    except Exception as e:
        print("write err", e)

if __name__ == "__main__":
    main()
