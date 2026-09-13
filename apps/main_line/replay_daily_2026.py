# -*- coding: utf-8 -*-
"""replay_daily_2026.py — 按真实交易日逐日全链路 gate 重放。"""
import json, os, glob, re
from collections import defaultdict
from fusion_mainline import THEME_CONCEPTS
from rotation_gate import decide
hist = json.load(open("data/concept_hist.json", encoding="utf-8"))
UMB = {"人工智能","AIGC概念","AI应用","AI智能体","AI语料","DeepSeek概念","ChatGPT概念","Kimi概念","智谱AI","多模态AI","AI眼镜"}
def parse_date(core):
    if re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", core): return core
    if re.match(r"^[0-9]{8}$", core): return core[:4]+"-"+core[4:6]+"-"+core[6:8]
    return None
def load_states(prefix, key):
    out = {}
    for p in sorted(glob.glob("data/%s_*.json" % prefix)):
        b = os.path.basename(p)
        d = parse_date(b[len(prefix)+1:-5])
        if not d: continue
        try:
            st = json.load(open(p, encoding="utf-8")); v = st.get(key)
            if v: out[d] = v
        except Exception: pass
    return out
mainline_states = load_states("main_line_state", "main_line")
wave_states = load_states("wave_state", "operation")
def fund(dt, name, days=10):
    dtn = dt.replace("-", "")
    for c, a in hist.items():
        if a.get("name") != name: continue
        z = list(zip(a.get("dates") or [], a.get("net_amount") or [])); s=0; n=0
        for dd, vv in reversed(z):
            if vv is None: continue
            if str(dd) > dtn: continue
            s += float(vv); n += 1
            if n >= days: break
        return s
    return 0
def healthy(main, dt, frac=0.1):
    cons = [c for c in (THEME_CONCEPTS.get(main) or []) if c]
    if not cons: return False, True, 0
    nets = {c: fund(dt, c) for c in cons}
    fine = [c for c in cons if c not in UMB] or cons
    mx = max([abs(nets[c]) for c in fine] or [0]); th = frac * mx
    sig_in = [c for c in fine if nets[c] > 0 and abs(nets[c]) >= th]
    if sig_in:
        ins = sum(nets[c] for c in sig_in); tp = max(nets[c] for c in sig_in); share = tp/ins if ins else 0
    else: share = 0
    return bool(len(sig_in) >= 2 and share < 0.8), bool(len(sig_in) <= 1), len(sig_in)
days = []
for c, a in hist.items():
    for d in a.get("dates") or []:
        d = str(d)
        if d >= "20250825":   # concept_hist 从2025-08-25起, 覆盖2025下半年+2026
            dd = d[:4]+"-"+d[4:6]+"-"+d[6:8]
            if dd not in days: days.append(dd)
days.sort()
TECH = {"AI/算力/科技", "半导体/芯片"}
def carry(states, dt):
    best = None
    for k, v in states.items():
        if k <= dt and (best is None or k > best[0]): best = (k, v)
    return best[1] if best else None
rows = []
for dt in days:
    main = carry(mainline_states, dt); wop = carry(wave_states, dt)
    if not main or not wop: continue
    h, s, nin = healthy(main, dt)
    ver = decide(wop, mainline_sucking=s, inside_mainline=True, rotation_healthy=h)["verdict"]
    rows.append({"date": dt, "main": main, "wave": wop, "healthy": h, "suck": s, "nin": nin, "verdict": ver, "tech": main in TECH})
json.dump({"rows": rows}, open("data/replay_daily_2026.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("n_days(有main+wave):", len(rows))
print("--- 科技动作月度汇总(各verdict天数) ---")
for m in sorted(set(r["date"][:7] for r in rows)):
    tr = [r for r in rows if r["tech"]]
    cnt = defaultdict(int)
    for r in tr: cnt[r["verdict"]] += 1
    if cnt: print(m, dict(cnt))
print("--- 科技 verdict 切换点 ---")
prev = None
for r in rows:
    if not r["tech"]: continue
    s = r["verdict"]
    if s != prev:
        print(r["date"], r["main"], r["wave"], "h=%s s=%s n=%d -> %s" % (r["healthy"], r["suck"], r["nin"], s))
        prev = s
print("--- 非科技主线 verdict 切点(医药/金融/资源等) ---")
prev = None
for r in rows:
    if r["tech"]: continue
    s = r["verdict"]
    if s != prev:
        print(r["date"], r["main"], r["wave"], "h=%s s=%s n=%d -> %s" % (r["healthy"], r["suck"], r["nin"], s))
        prev = s
