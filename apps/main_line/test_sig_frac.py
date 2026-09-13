# -*- coding: utf-8 -*-
"""test_sig_frac.py — 扫 data-backed 显著流入比例, 验证 healthy 稳健性(反过拟合)。"""
import json, os
hist = json.load(open("data/concept_hist.json", encoding="utf-8"))
from fusion_mainline import THEME_CONCEPTS
UMB = {"人工智能","AIGC概念","AI应用","AI智能体","AI语料","DeepSeek概念","ChatGPT概念","Kimi概念","智谱AI","多模态AI","AI眼镜"}
def fund(dt, name, days):
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
def raw(main, dt, frac, days=10, umb=True):
    cons = [c for c in (THEME_CONCEPTS.get(main) or []) if c]
    nets = {c: fund(dt, c, days) for c in cons}
    fine = ([c for c in cons if c not in UMB] or cons) if umb else cons
    mx = max([abs(nets[c]) for c in fine] or [0])
    th = frac * mx
    sig_in = [c for c in fine if nets[c] > 0 and abs(nets[c]) >= th]
    if sig_in:
        ins = sum(nets[c] for c in sig_in); tp = max(nets[c] for c in sig_in); share = tp/ins if ins else 0
    else: share = 0
    return bool(len(sig_in) >= 2 and share < 0.8), len(sig_in)
cases = [("2026-01-12","data/main_line_state_20260112.json"),("2026-01-14","data/main_line_state_20260114.json"),("2026-05-11","data/main_line_state_2026-05-11.json"),("2026-08-11","data/main_line_state_2026-08-11.json"),("2026-08-12","data/main_line_state_2026-08-12.json"),("2026-08-13","data/main_line_state_2026-08-13.json")]
fr = [0.05, 0.1, 0.15, 0.2, 0.3]
print("date | main | " + " | ".join("f=%.2f" % f for f in fr))
for dt, mlf in cases:
    ml = json.load(open(mlf, encoding="utf-8")); main = ml.get("main_line") or ""
    row = ["%s(%d)" % (h, nin) for h, nin in [raw(main, dt, f) for f in fr]]
    print("%s | %-10s | %s" % (dt, main, " | ".join(row)))
