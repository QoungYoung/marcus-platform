# -*- coding: utf-8 -*-
"""E1 v3：按**度**拆开比 —— 大级别标注 ↔ 周线档；子级别标注 ↔ 日线档（各自对各自 ✓）。

并修 v2 的映射错误：rung1（成功4-5）不能一律算 up ✗ —— 它有两种成因：
  · new_high60（创 60 日新高）⇒ 真上行 ⇒ up ✓
  · bottom_ok（止跌确认，还在调整区间里）⇒ 仍属 adjust ✓
"""
import importlib, json, os, sys
sys.path.insert(0, "apps/main_line")
import wave_ladder as W

LAB = json.load(open("data/_degree_labels.json", encoding="utf-8"))
BIG = {"大1浪", "大2浪", "大3浪", "大4浪", "主升级别", "趋势级别反转"}
SUB = {"3-2", "3-3", "4-2", "4-3", "4-4", "失败5", "末段", "C杀", "下跌1浪", "主跌1浪", "调整浪", "震荡"}


def load_weekly(code="000001.SH"):
    d = json.load(open("data/index_weekly_%s.json" % code.replace(".", "_"), encoding="utf-8"))
    return [(r[0], r[1], r[2], r[3]) for r in d["rows"]]


def pol_of(ev, weekly=False):
    ov = (ev.get("ov_spec") or {}).get("key") or ""
    if ov == "w_bottom" or ev.get("main_up") == "3-3":
        return "up"
    if ov in ("top_sell", "dtop"):
        return "down"
    if ov in ("basis_41", "basis_43"):
        return "adjust"
    r = ev["rung_index"]
    if r == 3:
        return "down"
    if r == 1:
        return "up" if (ev["features"] or {}).get("new_high60") else "adjust"
    return "adjust"


def run(env, rows, labels, tag):
    for k in ("WOLF_WAVE_LADDER_RALLY", "WOLF_WAVE_LADDER_HI", "WOLF_WAVE_LADDER_ORIGIN_BARS",
              "WOLF_WAVE_LADDER_MIN_BARS"):
        os.environ.pop(k, None)
    os.environ.update(env)
    importlib.reload(W)
    evs = W.walk(rows)
    hit = tot = 0
    base = 0
    from collections import Counter
    majors = Counter(L["pol"] for L in labels).most_common(1)[0]
    for L in labels:
        prev = [e for e in evs if e["date"] <= L["date"]]
        if not prev:
            continue
        tot += 1
        hit += 1 if pol_of(prev[-1]) == L["pol"] else 0
        base += 1 if L["pol"] == majors[0] else 0
    print("  %-26s 样本 %3d ｜ 吻合 %3d（%3.0f%%）｜ 多数类基线 %3.0f%%（%s）" % (
        tag, tot, hit, 100.0 * hit / max(tot, 1), 100.0 * base / max(tot, 1), majors[0]))
    return hit, tot


big = [L for L in LAB if L["kw"] in BIG]
sub = [L for L in LAB if L["kw"] in SUB]
print("标注拆分：大级别 %d 条 ｜ 子级别 %d 条（其余 %d 条不参与 ✓）" % (
    len(big), len(sub), len(LAB) - len(big) - len(sub)))
print("\n=== 大级别：周线（按度重标）vs 日线 ===")
run({"WOLF_WAVE_LADDER_RALLY": "26", "WOLF_WAVE_LADDER_HI": "104", "WOLF_WAVE_LADDER_ORIGIN_BARS": "200",
     "WOLF_WAVE_LADDER_MIN_BARS": "60"}, load_weekly(), big, "周线（大级别口径）")
run({}, W.load_rows(), big, "日线（生产口径）")
print("\n=== 子级别：日线 vs 周线 ===")
run({}, W.load_rows(), sub, "日线（生产口径）")
run({"WOLF_WAVE_LADDER_RALLY": "26", "WOLF_WAVE_LADDER_HI": "104", "WOLF_WAVE_LADDER_ORIGIN_BARS": "200",
     "WOLF_WAVE_LADDER_MIN_BARS": "60"}, load_weekly(), sub, "周线（大级别口径）")
