#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bt_pit_audit.py — **未来函数（前视）审计**：逐日检查回测快照与产出是否只用了 ≤ T-1 的数据。

检查项（每一项都能定位到具体文件/字段）：
  ① `_seed.json.pit_violations` 为空（seed 内已对滚动输入做过日期断言）；
  ② 滚动输入文件里**最大日期键 ≤ cut**（`theme_mf_daily` / `concept_hist` / `concept_long_seed` /
     `concept_vol` / `index_daily_000001`）——**主题资金流当日值绝不能进当日决策**；
  ③ 腿文件里 `cut < date`（布腿用的是前一交易日口径）；
  ④ 按日生产者产物带的是 `cut` 那一天（`trend_confirm_<cut>_long.json`、`rotation_crowding` 季度末 ≤ cut）；
  ⑤ 收益预览的估值末端 ≤ **跑批已处理日**（不能用数据源里最新那天给当前持仓估值）；
  ⑥ 抽查 `arm_*.log` 里钉住的时钟 = cut。

用法（仓库根）：`.venv/bin/python jobs/bt_pit_audit.py [--root data/_bt_year]`
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_env  # noqa: E402

ROLLING = ["theme_mf_daily.json", "concept_hist.json", "concept_long_seed.json",
           "concept_vol.json", "index_daily_000001.json"]
D8 = re.compile(r"(?<!\d)(20\d{6})(?!\d)")


def max_date_in(obj, depth=0, acc=None):
    """递归找 8 位日期字符串里的最大值。"""
    if acc is None:
        acc = []
    if depth > 5 or len(acc) > 200000:
        return acc
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and len(k) == 8 and k.isdigit():
                acc.append(k)
            max_date_in(v, depth + 1, acc)
    elif isinstance(obj, list):
        for v in obj[:5000]:
            if isinstance(v, str) and len(v) == 8 and v.isdigit():
                acc.append(v)
            elif isinstance(v, (dict, list)):
                max_date_in(v, depth + 1, acc)
    return acc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(bt_env.DATA, "_bt_year"))
    ap.add_argument("--max-days", type=int, default=0, help="只查最近 N 天（0=全部）")
    a = ap.parse_args()

    days = []
    for d in sorted(os.listdir(a.root)) if os.path.isdir(a.root) else []:
        p = os.path.join(a.root, d)
        if len(d) == 8 and d.isdigit() and os.path.isdir(p) and os.path.exists(os.path.join(p, "_seed.json")):
            days.append(d)
    if a.max_days:
        days = days[-a.max_days:]
    if not days:
        print("没有已完成的跑批日（需要 _seed.json）")
        return 1

    bad_total = 0
    print("== 未来函数审计：%d 个已完成交易日（%s → %s）" % (len(days), days[0], days[-1]))
    for d in days:
        sb = os.path.join(a.root, d)
        man = json.load(open(os.path.join(sb, "_seed.json"), encoding="utf-8"))
        cut = str(man.get("cut") or "")
        issues = []

        # ① seed 自检
        if man.get("pit_violations"):
            issues.append("① seed.pit_violations=%s" % man["pit_violations"])

        # ② 滚动输入最大日期键
        for nm in ROLLING:
            p = os.path.join(sb, nm)
            if not os.path.exists(p) or os.path.islink(p):
                continue
            try:
                obj = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            keys = max_date_in(obj)
            if keys:
                mx = max(keys)
                if cut and mx > cut:
                    issues.append("② %s 最大日期 %s > cut %s" % (nm, mx, cut))

        # ③ 腿文件的 cut 字段
        for fn in ("legs.jsonl", "legs_switch.jsonl"):
            p = os.path.join(sb, fn)
            if not os.path.exists(p):
                continue
            for ln in open(p, encoding="utf-8"):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    j = json.loads(ln)
                except Exception:
                    continue
                c2 = str(j.get("cut") or "")
                dd = str(j.get("date") or d)
                if c2 and c2 >= dd:
                    issues.append("③ %s 腿 cut=%s 不小于 date=%s" % (fn, c2, dd))

        # ④ 按日产物：① 文件名里的日期必须 ≤ cut；② 沙箱里**不得存在** date > cut 的按日文件
        #    （软链农场会把"当期"生产文件也链进来，而消费方常按"日期最大"取用 → 真前视）
        DATED_GLOBS = ("trend_confirm_*_long.json", "mainline_gate_*.json", "heat_v2_*.json",
                       "theme_r5_*.json", "chain_map_2*.json", "rotation_crowding_2*.json",
                       "wave_state_2*.json", "main_line_state_2*.json")
        for pat in DATED_GLOBS:
            for p in glob.glob(os.path.join(sb, pat)):
                m = D8.search(os.path.basename(p))
                if m and cut and m.group(1) > cut:
                    issues.append("④ 沙箱存在未来按日文件 %s（%s > cut %s）" % (os.path.basename(p), m.group(1), cut))
        rc = os.path.join(sb, "rotation_crowding.json")
        if os.path.exists(rc) and not os.path.islink(rc):
            try:
                q = str((json.load(open(rc, encoding="utf-8")) or {}).get("end_date") or "")
                if q and cut and q > cut:
                    issues.append("④ rotation_crowding end_date=%s > cut %s" % (q, cut))
            except Exception:
                pass

        # ⑥ 钉钟
        lp = os.path.join(a.root, "_summary", "arm_%s.log" % d)
        if os.path.exists(lp):
            head = open(lp, encoding="utf-8", errors="replace").read(2000)
            m = re.search(r"\[pin\] clock=(\d{8})", head)
            if m and cut and m.group(1) != cut:
                issues.append("⑥ arm 钉钟 %s ≠ cut %s" % (m.group(1), cut))

        if issues:
            bad_total += 1
            print("  ❌ %s（cut=%s）" % (d, cut))
            for x in issues[:6]:
                print("       - %s" % x)
    print("\n== 结论：%s" % ("✅ 全部 %d 天未发现未来函数迹象" % len(days) if not bad_total
                             else "❌ %d/%d 天存在问题（见上）" % (bad_total, len(days))))

    # ⑤ 预览/账户估值末端
    print("\n== 估值末端检查")
    accs = sorted(glob.glob(os.path.join(a.root, "_summary", "account_*.json")))
    if accs:
        for p in accs:
            cur = (json.load(open(p, encoding="utf-8")).get("curve") or [])
            end = cur[-1]["day"] if cur else "-"
            print("   %s 末端 %s %s" % (os.path.basename(p), end, "✅" if end <= days[-1] else "❌ 超过已处理日"))
    else:
        print("   （还没有账户层结果；粗估预览的末端由 bt_report.py 限定为跑批已处理日 = %s）" % days[-1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
