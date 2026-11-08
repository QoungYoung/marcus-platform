#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_forward_eval.py — P2.5 前向权重校准（只读）。

- 从 docs/wolf-vs-system-compare.md 取 2026 判例日，映射到 27 主题；
- 用主题主 source 的指数收益（1d/5d/20d/amount）做特征；
- 在 2026-01~09 的判例日上做权重网格，评估 top3 命中与 T+5 超额。
注意：这是研究脚本，不写生产配置。
"""
from __future__ import annotations
import itertools, os, sys
from collections import defaultdict

import numpy as np
import pandas as pd
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "jobs"))
from mainline_common import index_close, index_amount, pct, trade_dates  # noqa: E402
from core.tushare_relay import relay_query  # noqa: E402

THEME = os.path.join(ROOT, "config", "mainline", "generated", "universe_theme.yaml")
BROAD = {
    "半导体/芯片": ["storage", "semi_equipment", "semi_material", "chip_design", "packaging"],
    "AI/算力/科技": ["compute", "ai_app", "cpo", "optical_module", "communication_transmission", "liquid_cooling"],
    "机器人/智能制造": ["humanoid_robot", "robot_actuator"],
    "新能源/电池": ["battery", "pv"],
    "医药": ["innovative_drug", "medical_device"],
    "资源/周期": ["resources"],
    "军工/航天": ["space", "aviation", "satellite"],
    "金融": ["finance"],
    "稳增长/基建": ["construction"],
    "农业": ["agriculture"],
}


def load_cases():
    cases = {}
    for ln in open(os.path.join(ROOT, "docs", "wolf-vs-system-compare.md"), encoding="utf-8"):
        if not ln.startswith("| 2026"):
            continue
        c = [x.strip() for x in ln.strip("|").split("|")]
        if len(c) < 3:
            continue
        date = c[0].replace("-", "")
        themes = []
        for x in c[2].split("、"):
            themes += BROAD.get(x.strip(), [])
        if themes:
            cases[date] = sorted(set(themes))
    return cases


def main() -> None:
    cfg = yaml.safe_load(open(THEME, encoding="utf-8"))
    themes = {t["id"]: t for t in (cfg.get("themes") or []) + (cfg.get("satellite_themes") or [])}
    # 取每个主题可用历史最长的 source
    series = {}
    for tid, t in themes.items():
        for s in t.get("sources") or []:
            typ, code = s.get("type"), s.get("code")
            if not typ or not code:
                continue
            try:
                c = index_close(typ, code, "20260101", "20260911")
                if len(c) >= 40:
                    amt = index_amount(typ, code, "20260101", "20260911")
                    series[tid] = (c, typ, code, amt)
                    break
            except Exception:
                continue
    cases = load_cases()
    dates = sorted(d for d in cases if "20260101" <= d <= "20260904")
    bench = index_close("csi", "000001.SH", "20260101", "20260911")
    rows = []
    for d in dates:
        if d not in bench.index:
            continue
        # future 5d
        fut = [x for x in bench.index if x > d][:5]
        if len(fut) < 5:
            continue
        fwd = pct(float(bench.loc[fut[-1]]), float(bench.loc[d]))
        feats = {}
        for tid, (c, typ, code, amt) in series.items():
            if d not in c.index:
                continue
            hist = c.loc[:d]
            if len(hist) < 21:
                continue
            r1 = pct(float(hist.iloc[-1]), float(hist.iloc[-2]))
            r5 = pct(float(hist.iloc[-1]), float(hist.iloc[-6]))
            r20 = pct(float(hist.iloc[-1]), float(hist.iloc[0]))
            h = amt.loc[:d]
            a5 = float(h.tail(5).mean()) if len(h) >= 5 else float(h.mean() or 0)
            a20 = float(h.tail(20).mean()) if len(h) >= 20 else float(h.mean() or 0)
            feats[tid] = (r1, r5, r20, a5 / a20 if a20 > 0 else 1.0)
        if not feats:
            continue
        rows.append({"date": d, "expected": cases[d], "fwd_bench": fwd, "feats": feats,
                     "theme_series": {tid: v[0] for tid, v in series.items()}})
    print(f"cases={len(rows)} themes_with_series={len(series)}")
    if len(rows) < 10:
        print("cases too few"); return
    cut = max(5, int(len(rows) * 0.8))
    train, hold = rows[:cut], rows[cut:]
    print(f"train={len(train)} holdout={len(hold)} (holdout dates: {hold[0]['date']}..{hold[-1]['date']})")

    def evaluate(rws, w):
        hit3 = 0; exs = []
        for r in rws:
            vals = []
            for tid, (r1, r5, r20, amt) in r["feats"].items():
                vals.append((tid, w[0] * r1 + w[1] * r5 + w[2] * r20 + w[3] * (amt - 1)))
            vals.sort(key=lambda x: -x[1])
            top = [x[0] for x in vals[:3]]
            if set(top) & set(r["expected"]):
                hit3 += 1
            rets = []
            for tid in top:
                c = r["theme_series"][tid]
                fut = [x for x in c.index if x > r["date"]][:5]
                if len(fut) >= 5:
                    rets.append(pct(float(c.loc[fut[-1]]), float(c.loc[r["date"]])) - r["fwd_bench"])
            if rets:
                exs.append(sum(rets) / len(rets))
        return hit3 / len(rws) if rws else 0.0, float(np.mean(exs)) if exs else 0.0

    best = []
    for w1, w5, w20, wa in itertools.product([0, 0.2, 0.4], [0.2, 0.4, 0.6], [0, 0.1, 0.2], [0, 0.1, 0.2]):
        if w1 + w5 + w20 + wa <= 0:
            continue
        hit3, ex = evaluate(train, (w1, w5, w20, wa))
        best.append({"w": (w1, w5, w20, wa), "hit3": hit3, "fwd_excess": ex})
    best.sort(key=lambda x: (-x["hit3"], -x["fwd_excess"]))
    best_hit = best[0]["w"]
    candidates = {
        "current": (0.2, 0.4, 0.0, 0.1),
        "new": (0.0, 0.3, 0.2, 0.1),
        "c_0_0.2_0.2_0": (0.0, 0.2, 0.2, 0.0),
        "c_0.1_0.3_0.2_0.05": (0.1, 0.3, 0.2, 0.05),
        "c_0_0.4_0.2_0.05": (0.0, 0.4, 0.2, 0.05),
        "c_0.1_0.2_0.2_0.1": (0.1, 0.2, 0.2, 0.1),
        "best_hit": best_hit,
    }
    print("\nweights | train_hit3 train_fwd | holdout_hit3 holdout_fwd")
    for name, w in candidates.items():
        trh, tre = evaluate(train, w)
        hoh, hoe = evaluate(hold, w)
        print(f"{name} {w} | {trh:.2f} {tre:+.2%} | {hoh:.2f} {hoe:+.2%}")
    print("\ntop train variants:")
    for x in best[:5]:
        print(f"{x['w']} hit3={x['hit3']:.2f} fwd={x['fwd_excess']:+.2%}")


if __name__ == "__main__":
    main()
