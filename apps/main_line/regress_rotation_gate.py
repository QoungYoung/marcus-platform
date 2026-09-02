# -*- coding: utf-8 -*-
"""
regress_rotation_gate.py — 27 case 端到端回归：真实 wave/position 状态 → rotation_gate.decide()
期望输出来源 docs/p2-rotation-validation-analysis.md（gate v2 五分支 + B3 补丁）。
na/contextual case 跳过（纪律类/时点类不映射单一 gate 输出）。
用法: python apps/main_line/regress_rotation_gate.py [wave_json] [position_json]
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rotation_gate as rg
import validate_rotation_cases as vc

DATA = os.environ.get("DATA_DIR", "data")
def load(p):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return {}

# 期望 gate 输出（20 个有明确动作的 case；D2/D3 为时点/环境 context，跳过不判）
EXPECT = {
    "A1":"mainline_rotation", "A2":"mainline_rotation", "A3":"mainline_rotation",
    "A4":"block", "A5":"mainline_rotation", "A6":"defensive_reduce",
    "B1":"sell_guard", "B2":"mainline_rotation", "B3":"defense_mainline_rotation",
    "B4":"switch_low", "B5":"sell_guard",
    "C1":"switch_low", "C2":"defensive_reduce",
    "C4":"defensive_reduce", "C5":"defensive_reduce", "C6":"defensive_reduce",
    "D4":"block", "D5":"block", "D6":"block",
}
CONTEXT = {  # 非 gate 动作：仅参考
    "D2":"contextual(高切低环境识别)", "D3":"contextual(踩踏时点)", "C3":"na(执行底线)",
    "A5_extra": None,
}
NA = {"A5" if False else "", "C3", "D1", "E1", "E2", "E3", "E4"}

# 每 case 静态语境: (inside_mainline, mainline_sucking, rotation_healthy, not_distributed)
CTX = {
    "A1": (True, False, True, False), "A2": (True, True, True, False),
    "A3": (True, False, True, False), "A4": (True, True, True, False),
    "A5": (True, False, True, False), "A6": (False, False, True, False),
    "B1": (False, False, True, False), "B2": (True, False, True, False),
    "B3": (True, False, True, True),  "B4": (False, False, True, False),
    "B5": (False, False, True, False),
    "C1": (False, False, True, False), "C2": (False, False, True, False),
    "C4": (False, False, True, False), "C5": (False, False, True, False),
    "C6": (False, False, True, False),
    "D4": (True, True, True, False), "D5": (True, True, True, False),
    "D6": (False, False, False, False),
}
# A/B 持仓来源 target 关键词（取第一个 hit 构造 state）
A_TARGET = {"B1": ["航天", "卫星"], "B2": None, "B4": ["半导体"], "B5": ["半导体"]}
B_TARGET = {"C1": ["创新药", "减肥药", "中药"], "B4": None, "B3": None}
# 狼大自述"切到我的低位方向"但 position 回放未列具体候选 → 用通用 rel-low+资金入 候选(B4 假设, 报告中注明)
B_OVERRIDE = {"B4": {"position": "MID", "rel": "low", "fund": {"dir": "in", "conv": 1},
                     "structure": "flat", "struct_ok": True}}

def hit_state(h):
    return {"position": h.get("position"), "fund": {"dir": (h.get("fund") or {}).get("dir"),
                                                    "conv": (h.get("fund") or {}).get("conv", 0)},
            "structure": h.get("structure") or "flat"}

def pick(pid, kw_list, pos):
    for t in pos.get(pid, {}).get("targets", []):
        if t.get("kws") in (kw_list if isinstance(kw_list, list) else [kw_list]) or (kw_list and t.get("kws") == kw_list):
            pass
    # 宽松：遍历 targets 里 keywords 命中任一
    hits = []
    for t in pos.get(pid, {}).get("targets", []):
        kws = t.get("kws") or ""
        if isinstance(kws, str) and any(k in str(kws) for k in (kw_list or [])):
            hits += t.get("hits") or []
        elif isinstance(kws, list) and any(k in " ".join(kws) for k in (kw_list or [])):
            hits += t.get("hits") or []
    return hits

def best(hits, prefer_dir=None, prefer_rel=None):
    for h in hits:
        if "err" in h: continue
        ok_dir = (prefer_dir is None) or ((h.get("fund") or {}).get("dir") == prefer_dir)
        ok_rel = (prefer_rel is None) or (h.get("rel") == prefer_rel)
        if ok_dir and ok_rel: return h
    for h in hits:
        if "err" not in h: return h
    return None

def main(wave_p, pos_p):
    wave = load(wave_p); pos = load(pos_p)
    fails = []
    gated = [c["id"] for c in vc.CASES if c["id"] in EXPECT]
    print("wave cases=%d | gated=%d" % (len(wave), len(gated)))
    for cid in gated:
        c = next(c for c in vc.CASES if c["id"] == cid)
        w = wave.get(cid) or {}
        op = w.get("operation")
        if not op:
            fails.append((cid, "no wave op")); print("FAIL", cid, "no wave op"); continue
        inside, suck, healthy, nd = CTX[cid]
        a = None; b = None
        a_hits = pick(cid, A_TARGET.get(cid) or [], pos)
        if not a_hits and cid == "B5":  # B5 无自身 target，复用同日 B4 半导体位置
            a_hits = pick("B4", ["半导体"], pos)
        if a_hits:
            h = best(a_hits, prefer_dir="out")
            if h: a = hit_state(h)
        if B_OVERRIDE.get(cid):
            b = B_OVERRIDE[cid]
        elif B_TARGET.get(cid):
            bh = best(pick(cid, B_TARGET[cid], pos), prefer_dir="in", prefer_rel="low")
            if bh: b = hit_state(bh); b["rel"] = bh.get("rel")
        got = rg.decide(op, a=a, b=b, mainline_sucking=suck, inside_mainline=inside,
                        not_distributed=nd, rotation_healthy=healthy)["verdict"]
        exp = EXPECT[cid]
        ok = got == exp
        if not ok:
            fails.append((cid, "got=%s exp=%s op=%s a=%s b=%s" % (got, exp, op, json.dumps(a, ensure_ascii=False)[:80], json.dumps(b, ensure_ascii=False)[:80])))
        print("%s %-4s op=%-8s got=%-24s exp=%-24s" % ("OK " if ok else "FAIL", cid, op or "-", got, exp))
    print("\nPASS %d/%d FAIL %d" % (len(gated) - len(fails), len(gated), len(fails)))
    for f in fails: print("  FAIL:", f)
    return 1 if fails else 0

if __name__ == "__main__":
    wp = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "rotation_wave_replay.json")
    pp = sys.argv[2] if len(sys.argv) > 2 else os.path.join(DATA, "rotation_position_replay.json")
    raise SystemExit(main(wp, pp))
