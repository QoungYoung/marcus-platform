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
import os, sys, json, collections
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
}
META_GROUPS = {"科技/AI(总集)"}

def load_json(p):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return {}

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
    for sub, kws in SUB_UNIVERSE.items():
        rows = []
        for code, nm in names_by_code.items():
            if any(_norm(k) in _norm(nm) for k in kws):
                v = pos[code]
                fe = v.get("features") or {}
                fund = v.get("fund_flow") or {}
                rows.append({"name": nm, "position": v.get("position"), "action": v.get("action"),
                             "rel": fe.get("rel_mainline"), "box": fe.get("box_pos_pct"),
                             "vs1y": fe.get("vs_1y_high_pct"), "fund": fund.get("dir"),
                             "net": fund.get("strength") or 0})
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
    subs = list(SUB_UNIVERSE.keys())
    def netx(s):
        d = out_detail.get(s) or {}
        return (d.get("net_in_亿") or 0) - (d.get("net_out_亿") or 0)
    nets = {s: netx(s) for s in subs}
    abs_sum = sum(abs(v) for v in nets.values()) or 1
    top_s = max(subs, key=lambda s: abs(nets[s]))
    top1_share = abs(nets[top_s]) / abs_sum
    in_subs = [s for s in subs if nets[s] > 0]
    healthy = bool(len(in_subs) >= 2 and top1_share < 0.65)
    sucking = bool(top1_share >= 0.65 or len(in_subs) <= 1)
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
    maxc = max([(crowd_real.get(s) or {}).get("avg_float") or 0 for s in subs] or [0]) or 1
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
