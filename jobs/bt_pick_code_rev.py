# -*- coding: utf-8 -*-
"""bt_pick_code_rev.py — **用生产自己的日志校验/挑选逐日代码版本**（`--code-dir` 的自证）。

为什么要它（§9.20）：`data/_bt_code/rev_map.json` 是"git 提交日"映射，而生产可能是**从工作区部署**的
（含未提交改动）→ 某些天映射到的版本并不是当天实际在跑的代码。实测 09-10：映射版本 `rev_5a6327…` 的
`fusion_mainline.THEME_CONCEPTS` 只有 9 个主题、**没有农业** → `stock_confirm_judge` 走了固定 AI 兜底
（`{光通信模块: {theme: 农业}}`），确认域退化 → 腿级 0/8；而生产当天自己的日志写着
`TOP确认主题: ['农业','金融','稳增长/基建']` + `农业 > 农业种植 n=10` → 当天那份代码必然含农业。

判据（逐条来自生产日志，可机械核对）：
  ① `stock_confirm_refresh` 的 `TOP确认主题: [...]` ⊆ 候选版本 `fusion_mainline.THEME_CONCEPTS` 的键；
  ② `rotation_switch_arm` 的 `GATE_CONFIRMED_TODAY [...]` / `MAINLINE_POOL_TODAY [...]` ⊆ 同一组键（若有）。

用法（容器内）：
  python jobs/bt_pick_code_rev.py --day 20260910                      # 只校验映射版本 + 找满足的版本
  python jobs/bt_pick_code_rev.py --day 20260910 --all                # 扫全部版本树
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys

TREES = "/app/data/_bt_code"
SCHED = "/app/data/_bt_full/_sched"

PROBE = r'''
import os, sys, json
cdir = sys.argv[1]
sys.path[:0] = ["/app", "/app/apps/main_line", "/app/jobs", "/app/core"]
for sub in ("apps/main_line", "jobs", "backend", "core", "config"):
    p = os.path.join(cdir, sub)
    if os.path.isdir(p):
        sys.path.insert(0, p)
import fusion_mainline as fm
print(json.dumps({"file": fm.__file__, "n": len(fm.THEME_CONCEPTS),
                  "keys": sorted(fm.THEME_CONCEPTS.keys())}, ensure_ascii=False))
'''


def prod_expectations(day8: str):
    """从 scheduler 日志里取生产当天的"主题期望"。"""
    p = os.path.join(SCHED, "scheduler_%s-%s-%s.jsonl" % (day8[:4], day8[4:6], day8[6:]))
    exp = {"top_confirm": [], "gate_confirmed": [], "mainline_pool": []}
    if not os.path.exists(p):
        return exp
    for ln in open(p, encoding="utf-8", errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            j = json.loads(ln)
        except Exception:
            continue
        blob = (j.get("output") or "") + "\n" + (j.get("error") or "")
        for key, pat in (("top_confirm", r"TOP确认主题:\s*\[([^\]]*)\]"),
                         ("gate_confirmed", r"GATE_CONFIRMED_TODAY\s*\[([^\]]*)\]"),
                         ("mainline_pool", r"MAINLINE_POOL_TODAY\s*\[([^\]]*)\]")):
            for m in re.finditer(pat, blob):
                items = [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]
                if items:
                    exp[key] = items
    return exp


# 该日"必须存在"的生产脚本（否则树再"对主题"也不是当天的代码：实测 rev_b9266461 主题满足但
# 连 rotation_switch_arm.py / switch_builder.py / fusion_mainline.py 都没有 = 布腿链上线前的树）
REQUIRED = ["jobs/rotation_switch_arm.py", "apps/main_line/switch_builder.py",
            "apps/main_line/fusion_mainline.py", "apps/main_line/stock_confirm_judge.py"]


def tree_complete(cdir: str):
    miss = [f for f in REQUIRED if not os.path.exists(os.path.join(cdir, f))]
    return (not miss), miss


def probe_tree(cdir: str):
    try:
        r = subprocess.run([sys.executable, "-c", PROBE, cdir], capture_output=True, text=True, timeout=120)
        line = [l for l in (r.stdout or "").splitlines() if l.startswith("{")]
        return json.loads(line[-1]) if line else None
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--trees", default=TREES)
    ap.add_argument("--all", action="store_true", help="扫全部版本树（默认只校验映射版本 + 最近 8 个）")
    a = ap.parse_args()

    exp = prod_expectations(a.day)
    want = set(exp["top_confirm"]) | set(exp["gate_confirmed"]) | set(exp["mainline_pool"])
    print("生产日志期望（%s）：" % a.day)
    print("   TOP确认主题 =", exp["top_confirm"])
    print("   GATE_CONFIRMED_TODAY =", exp["gate_confirmed"], " MAINLINE_POOL_TODAY =", exp["mainline_pool"])
    if not want:
        print("⚠️ 该日日志里没有主题期望 → 无法自证（跳过）")
        return 0

    mapped = ""
    try:
        m = json.load(open(os.path.join(a.trees, "rev_map.json"), encoding="utf-8"))
        rev = ((m.get(a.day) or {}).get("rev")) or ""
        mapped = os.path.join(a.trees, "rev_" + rev) if rev else ""
    except Exception:
        pass

    cands = []
    if mapped and os.path.isdir(mapped):
        cands.append(mapped)
    others = sorted([d for d in glob.glob(os.path.join(a.trees, "rev_*")) if os.path.isdir(d)],
                    key=lambda p: os.path.getmtime(p), reverse=True)
    cands += [d for d in others if d not in cands][:(len(others) if a.all else 8)]

    ok_rev = None
    for d in cands:
        info = probe_tree(d)
        if not info:
            continue
        keys = set(info["keys"])
        miss = sorted(want - keys)
        _complete, _missfiles = tree_complete(d)
        if not _complete:
            tag = "❌ 缺关键脚本 %s" % [m.split("/")[-1] for m in _missfiles][:3]
        elif miss:
            tag = "❌ 缺主题 %s" % miss[:4]
        else:
            tag = "✅ 满足（主题+脚本齐全）"
        print("   %-46s 主题%2d  %s" % (os.path.basename(d), info["n"], tag))
        if not miss and _complete and ok_rev is None:
            ok_rev = d
    print()
    if ok_rev is None:
        print("⚠️ 没有候选版本同时满足期望主题 → 建议：用现行代码跑并显式标注近似（或补历史版本树）")
        return 0
    print("→ 建议 --code-dir %s" % ok_rev)
    if mapped and ok_rev != mapped:
        print("⚠️ 与 rev_map 映射的版本不同（%s）→ 该日 git 映射不可信，回测请显式指定" % os.path.basename(mapped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
