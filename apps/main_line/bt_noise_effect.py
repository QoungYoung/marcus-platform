# -*- coding: utf-8 -*-
"""bt_noise_effect.py — 验证三合一降噪(10日平滑+门槛+TOP-N+确认制)对 rotation_healthy 的降噪效果
对比 旧口径(快照5日/≥2流入+top1_share<0.65 计数依赖) vs 新口径(10日+ROT_NET_MIN门槛+ROT_TOP_N=3+ROT_CONFIRM_DAYS=2确认制)
在 44 个已知主线日期 上统计 healthy 日间翻转次数(旧 vs 新), 及 per-concept/skeleton/old 三种粒度结论是否一致。
无 dsh 调用(确定性, 快速)。
用法: python -u apps/main_line/bt_noise_effect.py
"""
import os, sys, json, glob, re, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from fusion_mainline import THEME_CONCEPTS
except Exception:
    THEME_CONCEPTS = {}
try:
    from rotation_universe import SUB_UNIVERSE, META_GROUPS, _norm, DATA
except Exception:
    SUB_UNIVERSE = {}; META_GROUPS = set(); _norm = lambda s: str(s).replace(" ", "").replace("　", ""); DATA = os.environ.get("DATA_DIR", "data")
GATE = float(os.getenv("ROT_NET_MIN", "0")); TOPN = int(os.getenv("ROT_TOP_N", "3")); CONFIRM = int(os.getenv("ROT_CONFIRM_DAYS", "3")); DAYS = int(os.getenv("ROT_NET_DAYS", "10"))
UMBRELLA = {"人工智能", "AIGC概念", "AI应用", "AI智能体", "AI语料", "DeepSeek概念", "ChatGPT概念", "Kimi概念", "智谱AI", "多模态AI", "AI眼镜"}

def load(p):
    try: return json.load(open(os.path.join(DATA, p), encoding="utf-8"))
    except Exception: return {}

def fund_at(hist, date, kws, days=DAYS):
    tot = 0.0
    for c, a in hist.items():
        if not isinstance(a, dict) or not isinstance(a.get("name"), str): continue
        nm = a["name"]
        if not any(_norm(k) in _norm(nm) for k in kws): continue
        dates = a.get("dates") or []; net = a.get("net_amount") or []
        s = 0.0; cnt = 0
        dtn = str(date).replace("-", "")   # concept_hist 日期是 YYYYMMDD, 归一后再比较
        for d, v in reversed(list(zip(dates, net))):
            if v is None: continue
            if str(d) > dtn: continue
            s += float(v); cnt += 1
            if cnt >= days: break
        tot += s
    return tot

def _nets(hist, date, SU):
    return {sub: fund_at(hist, date, kws) for sub, kws in SU.items() if sub not in META_GROUPS}

def old_healthy(nets):
    subs = list(nets.keys())
    if not subs: return False
    abs_sum = sum(abs(v) for v in nets.values()) or 1
    top_s = max(subs, key=lambda s: abs(nets[s]))
    share = abs(nets[top_s]) / abs_sum
    return bool(len([s for s in subs if nets[s] > 0]) >= 2 and share < 0.65)

def new_healthy(nets):
    """生产口径: 剔除伞概念 + ≥2个显著净流入子方向 + 流入分布不集中。"""
    subs = list(nets.keys())
    if not subs: return False
    fine = [s for s in subs if s not in UMBRELLA] or subs
    sig = [s for s in fine if abs(nets.get(s, 0)) >= GATE] or fine
    sig_in = [s for s in sig if nets[s] > 0]
    if sig_in:
        ins = sum(nets[s] for s in sig_in); tp = max(nets[s] for s in sig_in)
        share = tp / ins if ins else 0.0
    else:
        share = 0.0
    return bool(len(sig_in) >= 2 and share < 0.8)

class Hyst:
    """跨日期确认制: 连续 CONFIRM 次同值才翻转。"""
    def __init__(self):
        self.conf = None; self.pend = None; self.pend_count = 0
    def feed(self, raw):
        if self.conf is None:
            self.conf = raw; self.pend = None; self.pend_count = 0; return self.conf
        if raw == self.conf:
            self.pend = None; self.pend_count = 0; return self.conf
        if raw == self.pend:
            self.pend_count += 1
        else:
            self.pend = raw; self.pend_count = 1
        if self.pend_count >= CONFIRM:
            self.conf = raw; self.pend = None; self.pend_count = 0
        return self.conf

def date_of(p):
    b = os.path.basename(p)
    if b.startswith("main_line_state_") and b.endswith(".json"):
        core = b[len("main_line_state_"):-5]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", core): return core
    return None

def per_concept(main):
    return {c: [c] for c in (THEME_CONCEPTS.get(main) or []) if c}

def skeleton(main, cons):
    cset = set(_norm(c) for c in cons); skel = {}
    for sub, kws in SUB_UNIVERSE.items():
        if sub in META_GROUPS: continue
        if any(any(_norm(k) in cs or cs in _norm(k) for k in kws) for cs in cset): skel[sub] = kws
    out = {}; assigned = set()
    for c in cons:
        nc = _norm(c); best = None; bests = 0
        for sub, kws in skel.items():
            hit = sum(1 for k in kws if _norm(k) in nc or nc in _norm(k))
            if hit > bests: bests = hit; best = sub
        if best: out.setdefault(best, []).append(c); assigned.add(nc)
        else: out.setdefault(c, []).append(c); assigned.add(nc)
    return out

def main():
    hist = load("concept_hist.json")
    files = sorted([p for p in glob.glob(os.path.join(DATA, "main_line_state_*.json"))])
    rows = []
    for p in files:
        d = date_of(p)
        if not d: continue
        st = json.load(open(p, encoding="utf-8"))
        main = st.get("main_line") or ""
        if not main: continue
        cons = THEME_CONCEPTS.get(main) or []
        if not cons: continue
        pc = per_concept(main); skel = skeleton(main, cons)
        old_su = {s: kws for s, kws in SUB_UNIVERSE.items() if s not in META_GROUPS}
        rows.append({"date": d, "main": main, "pc": _nets(hist, d, pc),
                     "skel": _nets(hist, d, skel), "old": _nets(hist, d, old_su)})
    # 按日期排序(chronological)
    rows.sort(key=lambda r: r["date"])
    # 旧口径 healthy(逐日) + 新口径(逐日 + 确认制)
    h_old = Hyst(); h_new_pc = Hyst(); h_new_skel = Hyst(); h_new_old = Hyst()
    res = []
    for r in rows:
        old_raw = old_healthy(r["pc"])
        new_pc = new_healthy(r["pc"]); new_skel = new_healthy(r["skel"]); new_old = new_healthy(r["old"])
        conf_old = h_old.feed(old_raw)
        conf_new_pc = h_new_pc.feed(new_pc); conf_new_skel = h_new_skel.feed(new_skel); conf_new_old = h_new_old.feed(new_old)
        res.append({"date": r["date"], "main": r["main"], "old_raw": old_raw, "old_conf": conf_old,
                    "new_pc_raw": new_pc, "new_pc_conf": conf_new_pc,
                    "new_skel_conf": conf_new_skel, "new_old_conf": conf_new_old})
    # 统计: 旧 vs 新 的 healthy 翻转次数(相邻确认值变化) + 一致性
    def flips(key): return sum(1 for i in range(1, len(res)) if res[i][key] != res[i-1][key])
    def agree(keys):
        same = sum(1 for r in res if len({r[k] for k in keys}) == 1)
        return same / max(len(res), 1)
    out = {"ts": datetime.datetime.now().isoformat(), "n_dates": len(res),
           "old_conf_flips": flips("old_conf"), "new_pc_conf_flips": flips("new_pc_conf"),
           "new_skel_conf_flips": flips("new_skel_conf"), "new_old_conf_flips": flips("new_old_conf"),
           "pc_vs_skel_agree": agree(["new_pc_conf", "new_skel_conf"]),
           "pc_vs_old_agree": agree(["new_pc_conf", "new_old_conf"]),
           "rows": res}
    json.dump(out, open(os.path.join(DATA, "bt_noise_effect.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("n_dates=%d old_conf_flips=%d new_pc_flips=%d new_skel_flips=%d new_old_flips=%d | pc-vs-skel agree=%.0f%% pc-vs-old agree=%.0f%%" % (
        len(res), out["old_conf_flips"], out["new_pc_conf_flips"], out["new_skel_conf_flips"], out["new_old_conf_flips"],
        out["pc_vs_skel_agree"]*100, out["pc_vs_old_agree"]*100), flush=True)
    for r in res:
        print("%-12s %-14s oldC=%d newPC=%d newSkel=%d newOld=%d" % (
            r["date"], r["main"], int(r["old_conf"]), int(r["new_pc_conf"]), int(r["new_skel_conf"]), int(r["new_old_conf"])), flush=True)
    return out

if __name__ == "__main__":
    main()
