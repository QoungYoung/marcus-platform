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
SUB_UNIVERSE = {
    "国算/算力": ["算力概念", "数据中心", "云计算", "大数据", "国资云概念"],
    "液冷": ["液冷概念"],
    "存储": ["存储芯片"],
    "材料": ["新材料", "碳基材料", "PEEK材料概念"],
    "大芯(拥挤侧)": ["半导体概念", "国产芯片", "AI芯片", "第三代半导体", "第四代半导体"],
    "大光(拥挤侧)": ["光通信模块", "CPO概念", "光纤概念"],
    "铜缆/电源": ["铜缆高速连接"],
}

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
            if any(k in nm for k in kws):
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
        out_detail[sub] = {"n": len(rows), "net_in_亿": round(net, 1), "net_out_亿": round(net_out, 1),
                           "rel_high_n": rel_hi, "rel_low_n": rel_lo,
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
    crowded = sorted(subs, key=lambda s: ((out_detail.get(s) or {}).get("crowd", 0) + (0.2 if "拥挤侧" in s else 0),
                                          1 if "拥挤侧" in s else 0), reverse=True)[:3]
    room_cand = [s for s in subs if "拥挤侧" not in s and (((out_detail.get(s) or {}).get("rel_lo_n") or 0) >= 1
                 or (nets.get(s, 0) > 0 and ((out_detail.get(s) or {}).get("crowd") or 0) < 0.5))]
    room_cand = [s for s in room_cand if s not in crowded][:3]
    return {"mainline_sucking": sucking, "rotation_healthy": healthy,
            "top1_sub": top_s, "top1_share": round(top1_share, 2), "inflow_subs": in_subs,
            "crowded_top": crowded, "room_bottom": room_cand, "detail": out_detail}

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
