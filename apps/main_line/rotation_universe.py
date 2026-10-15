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


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass

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
    except Exception as _e_sil1:
        _silent_alert("rotation_universe.py:61", _e_sil1)

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
    except Exception as _e_sil2:
        _silent_alert("rotation_universe.py:115", _e_sil2)

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

def chain_momentum_top(hist, subs, n, meta_groups=None):
    """按**过去 5 日涨幅**取前 n 条概念（`concept_hist.json` 的 close 序列 ✓，纯函数便于单测 ✓）。

    依据（离线实测 127 天 ✓）：链动量 IC=+0.133（t=+3.3）显著 ✓ vs `space_score` IC=−0.053（噪声 ✗）。
    返回 [(概念名, 5日收益率), ...]（降序 ✓）；数据不足的概念跳过 ✓，不抛异常 ✓。
    """
    if not n or n <= 0 or not isinstance(hist, dict):
        return []
    mg = set(meta_groups or ())
    name2ret = {}
    for _bk, _v in hist.items():
        if not isinstance(_v, dict):
            continue
        nm = _norm(str(_v.get("name") or _bk))
        cs = [c for c in (_v.get("close") or []) if isinstance(c, (int, float)) and c > 0]
        if nm and len(cs) >= 6 and cs[-6] > 0:
            name2ret[nm] = cs[-1] / cs[-6] - 1.0
    rank = sorted([s for s in (subs or []) if s not in mg and s in name2ret],
                  key=lambda s: name2ret[s], reverse=True)[:n]
    return [(s, name2ret[s]) for s in rank]


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
    except Exception as _e_sil3:
        _silent_alert("rotation_universe.py:235", _e_sil3)
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
    # ── 「强势/动量」通道（2026-10-15 ✓ **开关默认 0 ⇒ 逐字不变** ✓）────────────────────
    #   依据（离线实测 127 天 ✓）：
    #     · `space_score`（低位/跌幅）与未来 5 日链收益 **IC=−0.053（t=−1.3）** ✗ = 噪声；
    #       **链动量（过去 5 日涨幅）IC=+0.133（t=+3.3）** ✓ = 唯一显著的选链因子 ✓
    #     · 现状 ⇒ `room_bottom` 127 天里**龙头所在链 0 次进榜** ✗
    #       （榜上是 ChatGPT概念/AI语料/AIGC/券商/免税 ✗）；换动量口径后龙头链进榜 **56 次** ✓（0.59/天 ✓）
    #     · 与语料一致 ✓：狼大 2025-07-29「**下跌浪只做龙头**」／2025-05-19「买就买强的 千万不要买后排补涨」✓
    #   实现：取**前 N 条动量链**（`WOLF_CHAIN_MOM_N`，默认 2 ✓）**前置**到 `room_cand` ✓
    #     ⇒ `bt_day_legs.py` 的 `buy_chains[:2]`（硬编码取前 2 ✗）会**先取到它们** ✓，
    #       从而让"选票本来就按 leader 排序 ✓"的路径 A 真正去选龙头 ✓ —— 顺带也让
    #       `ma_line_price`（要求"收盘下方有均线" ✓）在强势股上能取到线位 ⇒ 255 线组买点得以生效 ✓
    #   取数：`concept_hist.json` 的**各概念 close 序列**（种子已按 as-of 截断 ✓ 零额外网络 ✓）。
    try:
        _momN = int(float(os.getenv("WOLF_CHAIN_MOM_N", "0") or 0))
    except (TypeError, ValueError):
        _momN = 0
    if _momN > 0:
        try:
            _rank = chain_momentum_top(load_json(HIST) or {}, subs, _momN, META_GROUPS)
            if _rank:
                _names = [s for s, _r in _rank]
                room_cand = [s for s in _names if s not in room_cand] + room_cand
                print("[rotation] CHAIN_MOM 前置 %d 条动量链: %s（5日 %s）"
                      % (len(_rank), _names, ["%.1f%%" % (r * 100) for _s, r in _rank]), flush=True)
        except Exception as _e_mom:
            _silent_alert("rotation_universe.py:chain_mom", _e_mom)
    crowded_represent = []
    try:
        cf = json.load(open(os.path.join(DATA, "rotation_crowding.json"), encoding="utf-8"))
        for key in ("chip_top", "optics_top"):
            for it in (cf.get(key) or [])[:2]:
                nm = it.get("name") or it.get("symbol")
                crowded_represent.append("%s(%s)" % (nm, it.get("symbol")))
    except Exception as _e_sil4:
        _silent_alert("rotation_universe.py:268", _e_sil4)
    return {"mainline_sucking": sucking, "rotation_healthy": healthy,
            "top1_sub": top_s, "top1_share": round(top1_share, 2), "inflow_subs": in_subs,
            "crowded_top": crowded, "holdT_top": holdT, "room_bottom": room_cand, "detail": out_detail,
            "sub_scoring": scores, "crowding_real": bool(crowd_real), "crowded_represent": crowded_represent}

# ── build_crowding_blacklist() 已于 2026-09-13 真删（用户指令，非停用）──
# 原函数：拥挤无空间子方向成分股黑名单 → data/crowding_blacklist.json（个股级 v2：
#   n_funds≥4 且 Σfloat≥1% 判「公募核心拥挤」），供 backend/app/api/indicator.py 的
#   check_entry_filters 硬拦、以及 wolf_confirm_pick / rotation_switch_arm /
#   rotation_switch_agent 的静默排除使用。
# 删除依据：jobs/eval_d15_crowd_blacklist.py 的事件研究（docs/wolf-d15-crowd-blacklist-event-study.md）
#   显示被拦组未来 5 日 +3.689% vs 非拥挤 +0.250%（块状 t=1.39 不显著）→ 整体拦反；
#   且「公募重仓」这条腿无统计支撑（D11：主题层 IC≈0、个股层 IC +0.019、加排除后 +0.683%→−0.152%）。
# 注意：build_crowding.py 仍在产出 data/rotation_crowding.json（rotation 评分用），那是另一套机制，未删。

def main():
    out = proxies()
    print(json.dumps(out, ensure_ascii=False, indent=1)[:4000])
    try:
        json.dump(out, open(os.path.join(DATA, "rotation_universe_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("WROTE", os.path.join(DATA, "rotation_universe_result.json"))
    except Exception as e:
        print("write err", e)

if __name__ == "__main__":
    main()
