# -*- coding: utf-8 -*-
"""bt_input_divergence.py — 逐日比对"**回放沙箱的中间输入**" vs "**生产当天自己打印的输入**"。

为什么需要：腿级对不上的时候，光看 `legs.jsonl` 只能知道"少了/多了"，不知道**第一个分叉在哪**。
生产的 `logs/scheduler_<date>.jsonl` 里，`rotation_universe_refresh`（08:05）会把自己的产物打全：
  `derive: WROTE rotation_sub_universe.json main=半导体/芯片 themes=2 subs=25 version=v0 | [ ...25 个概念... ] | { ...池 JSON... }`
→ 拿它跟沙箱里的 `rotation_sub_universe.json` / `rotation_universe_result.json` 一比，
就能直接指出"是 **main_line 取错版本**（Stale）还是池算错"。

用法（容器内）：
  python jobs/bt_input_divergence.py --sched /app/data/_bt_full/_sched --root /app/data/_bt_sep \
      --days 20260908,20260909,20260910,20260911,20260914
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys


def prod_universe_from_log(sched_dir: str, day8: str):
    """从 scheduler 日志里取生产 08:05 `rotation_universe_refresh` 的产物（main / subs / 池 JSON）。"""
    p = os.path.join(sched_dir, "scheduler_%s-%s-%s.jsonl" % (day8[:4], day8[4:6], day8[6:]))
    if not os.path.exists(p):
        return None
    out = None
    for ln in open(p, encoding="utf-8", errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            j = json.loads(ln)
        except Exception:
            continue
        if j.get("task_id") == "rotation_universe_refresh" and "derive: WROTE" in (j.get("output") or ""):
            out = j
    if not out:
        return None
    txt = out["output"]
    main = re.search(r"main=([^\s]+)", txt)
    subs_n = re.search(r"subs=(\d+)", txt)
    themes = re.search(r"themes=(\d+)", txt)
    # 第一段 [ ... ] = 子概念清单
    subs = []
    m = re.search(r"\|\s*\[(.*?)\]\s*\|", txt, re.S)
    if m:
        try:
            subs = ast.literal_eval("[" + m.group(1) + "]")
        except Exception:
            subs = [x.strip().strip('",') for x in m.group(1).split("\n") if x.strip().strip('",')]
    # 池 JSON 在日志里可能被截断 → 不退化为"整体解析"，只逐个抓需要的字段
    pool = {}
    for key in ("top1_sub", "top1_share", "room_bottom", "crowded_top", "inflow_subs",
                "holdT_top", "mainline_sucking", "rotation_healthy"):
        m3 = re.search(r'"%s":\s*(\[[^\]]*\]|[^,\n}]+)' % key, txt)
        if not m3:
            continue
        raw = m3.group(1).strip().rstrip(',')
        try:
            pool[key] = ast.literal_eval(raw)
        except Exception:
            pool[key] = raw.strip('"')
    return {"started_at": out.get("started_at"), "main": main.group(1) if main else None,
            "n_subs": int(subs_n.group(1)) if subs_n else None,
            "n_themes": int(themes.group(1)) if themes else None,
            "subs": subs, "pool": pool}


def replay_universe(root: str, day8: str):
    su = os.path.join(root, day8, "rotation_sub_universe.json")
    ru = os.path.join(root, day8, "rotation_universe_result.json")
    a = json.load(open(su, encoding="utf-8")) if os.path.exists(su) else {}
    b = json.load(open(ru, encoding="utf-8")) if os.path.exists(ru) else {}
    return {"main": a.get("main_line"), "ts": a.get("ts"),
            "subs": sorted((a.get("subs") or {}).keys()), "n_subs": len(a.get("subs") or {}),
            "room_bottom": b.get("room_bottom"), "top1_sub": b.get("top1_sub"),
            "crowded_top": b.get("crowded_top"), "inflow_subs": b.get("inflow_subs")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sched", default="/app/data/_bt_full/_sched")
    ap.add_argument("--root", default="/app/data/_bt_sep")
    ap.add_argument("--days", required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    days = [d.strip() for d in a.days.split(",") if d.strip()]
    res = {}
    for d in days:
        pr = prod_universe_from_log(a.sched, d)
        rp = replay_universe(a.root, d)
        row = {"prod": pr, "replay": rp}
        if pr:
            row["same_main"] = (pr.get("main") == rp.get("main"))
            row["subs_only_prod"] = sorted(set(pr.get("subs") or []) - set(rp.get("subs") or []))
            row["subs_only_replay"] = sorted(set(rp.get("subs") or []) - set(pr.get("subs") or []))
            if pr.get("pool") and rp.get("room_bottom") is not None:
                row["room_same"] = (list(pr["pool"].get("room_bottom") or []) == list(rp.get("room_bottom") or []))
        res[d] = row

        print("== %s" % d)
        if not pr:
            print("   生产：该日无 rotation_universe_refresh 日志")
        else:
            print("   生产 main=%s subs=%s themes=%s room_bottom=%s"
                  % (pr.get("main"), pr.get("n_subs"), pr.get("n_themes"),
                     (pr.get("pool") or {}).get("room_bottom")))
        print("   回放 main=%s subs=%s ts=%s room_bottom=%s"
              % (rp.get("main"), rp.get("n_subs"), rp.get("ts"), rp.get("room_bottom")))
        if pr:
            print("   → main %s | room_bottom %s | 仅生产子概念 %s | 仅回放子概念 %s"
                  % ("✅一致" if row.get("same_main") else "❌不一致",
                     "✅一致" if row.get("room_same", "?") is True else "❌不一致",
                     row["subs_only_prod"][:6], row["subs_only_replay"][:6]))
    if a.out:
        json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n→", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
