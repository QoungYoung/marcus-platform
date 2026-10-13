# -*- coding: utf-8 -*-
"""A/B 前置检查（§9.822）：**跑臂前**先确认所选窗口里"要翻的那个开关"真的有拦截对象 ✓

教训（用户 2026-10-13 指出 ✗）：我先跑了一个 0909→0930 窗口的 A/B，而那两个开关在该窗口**0 次拦截** ✗
 ⇒ 三条臂完全一样、白跑约 3 小时 ✗
根因 ✗：我拿的是 **c 臂（0601→0828）** 的拦截计数（做T时段窗 264 / 底仓floor 93 ✓），
      却把它们**当成了 0909→0930 窗口**的数 ✗（t41 同窗口里这两项都是 0 ✓——这个数就在我自己的报告里 ✗）

用法：`.venv/bin/python scripts/ab_precheck.py <臂tag> <START> <END>`
输出：该窗口内 **各拦截家族计数** ✓ + 候选开关的可用性判定（PASS/FAIL ✓）
"""
import glob
import json
import os
import sys
from collections import Counter

LEVERS = {
    "WOLF_TURNOVER_GUARD=0（关做T时段窗）": "做T时段窗",
    # ★ B 闸豁免是**按腿型**生效 ⇒ 必须看"该家族拦截里有哪些腿型" ✓（否则会像 t42c 那样空跑 ✗）
    "WOLF_FALLING_GATE_EXEMPT_LEGS=wolf_ambush_buy,trend_break_buy": ("B/C 时点闸",
        ("wolf_ambush_buy", "trend_break_buy")),
    "WOLF_BASE_FLOOR（底仓 floor）": "底仓floor",
    "WOLF_ADDON_CAP（加仓口径）": "加仓口径",
    "WOLF_TURNOVER_GUARD=0（其余换手闸：同价来回/下去不补）": "换手其它",
}
MIN_HITS = 5


def fam(r):
    if any(k in r for k in ("无弹药", "无底仓", "无券", "无可卖", "可为 0", "可卖 0", "裸空", "as-of")):
        return "无弹药/无底仓"
    if "做T时段窗" in r:
        return "做T时段窗"
    if "COND-TIMING" in r:
        return "B/C 时点闸"
    if "floor" in r or "收敛" in r:
        return "底仓floor"
    if "加仓口径" in r:
        return "加仓口径"
    if "同价来回" in r or "下去不补" in r or "同价重复" in r or "同价" in r:
        return "换手其它"
    if "浪型" in r or "准入" in r or "档位" in r:
        return "浪型/准入"
    return "其它"


def main():
    tag, start, end = (sys.argv[1:4] + ["", "", ""])[:3]
    if not tag:
        print("用法: ab_precheck.py <臂tag> <START> <END>")
        return
    c = Counter(); days = 0; ex = 0
    for fp in sorted(glob.glob("data/_bt_%s/_summary/prod_*.json" % tag)):
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        day = str(d.get("day") or "")
        if start and end and not (start <= day <= end):
            continue
        days += 1
        for t in (d.get("triggers") or []):
            if str(t.get("status")) == "executed":
                ex += 1
            if str(t.get("status")) in ("blocked", "cancelled"):
                _f = fam(str(t.get("reason") or ""))
                c[_f] += 1
                c["%s|%s" % (_f, str(t.get("event_type")))] += 1
    print("臂 %s ｜窗口 %s→%s ｜ %d 天 ｜ 期间 executed=%d" % (tag, start or "-", end or "-", days, ex))
    tot = sum(c.values()) or 1
    print("\n该窗口内的拦截家族：")
    for k, v in c.most_common():
        print("  %-16s %5d（%4.1f%%）" % (k, v, 100.0 * v / tot))
    print("\n候选开关可用性（该窗口至少 %d 次拦截才算「有对象」✓）：" % MIN_HITS)
    ok_any = False
    for lever, spec in LEVERS.items():
        if isinstance(spec, tuple):
            f, kinds = spec
            n = sum(c.get("%s|%s" % (f, k), 0) for k in kinds)
        else:
            f, kinds = spec, None
            n = c.get(f, 0)
        flag = "✅ PASS（对象 %d 次 ✓）" % n if n >= MIN_HITS else "⛔ FAIL（对象 %d 次 ✗ 白跑）" % n
        ok_any = ok_any or n >= MIN_HITS
        print("  %-52s %s" % (lever[:52], flag))
    if any(isinstance(v, tuple) for v in LEVERS.values()):
        print("\nB/C 时点闸拦截的腿型分布（判豁免名单是否有对象 ✓）：")
        for k, v in c.most_common():
            if k.startswith("B/C 时点闸|"):
                print("    %-34s %d" % (k.split("|", 1)[1], v))
    print("\n⇒ %s" % ("至少一个开关可用 ✓ 可以开跑" if ok_any else
                    "该窗口**没有任何可用开关** ✗ ⇒ 必须换窗口（先找老臂里拦截多的时段 ✓）"))


if __name__ == "__main__":
    main()
