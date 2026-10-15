# -*- coding: utf-8 -*-
"""ab_wave_ladder_vs_prod.py — 影子双跑：浪型链的 operation vs 生产 wave_state.operation（历史快照）。

对照集：data/_bt_year/<YYYYMMDD>/wave_state.json（179 天回测落盘的生产浪型档），
逐日**因果**算浪型链的 op（只用 ≤ 当日的数据），统计一致率与分歧，并打印分歧样例 + 浪型链给的理由。

用法::

    python jobs/ab_wave_ladder_vs_prod.py            # 默认 data/_bt_year
    python jobs/ab_wave_ladder_vs_prod.py --dir data/_bt_b56 --limit 20
"""
import argparse
import collections
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "apps", "main_line")):
    if p not in sys.path:
        sys.path.insert(0, p)

import wave_ladder as W  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/_bt_year")
    ap.add_argument("--limit", type=int, default=12)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, "*", "wave_state.json")))
    if not files:
        print("无对照快照：", args.dir)
        return 0
    rows = W.load_rows()
    if not rows:
        print("无指数数据 ⇒ fail-open")
        return 0

    pairs = collections.Counter()
    diverged = []
    skipped = 0
    for f in files:
        day = os.path.basename(os.path.dirname(f))
        try:
            prod = (json.load(open(f, encoding="utf-8")) or {}).get("operation") or "?"
        except Exception:
            skipped += 1
            continue
        ev = W.evaluate(rows, day)
        if not ev:
            skipped += 1
            continue
        pairs[(prod, ev["operation"])] += 1
        if prod != ev["operation"]:
            diverged.append((day, prod, ev["operation"], ev["reasons"]))

    tot = sum(pairs.values())
    agree = sum(n for (a, b), n in pairs.items() if a == b)
    print("对照：%s  快照 %d 天（跳过 %d）" % (args.dir, tot, skipped))
    print("一致率：%d/%d = %.1f%%" % (agree, tot, 100.0 * agree / max(tot, 1)))
    print("\n混淆（生产 op → 浪型链 op）：")
    for (a, b), n in sorted(pairs.items(), key=lambda kv: -kv[1]):
        flag = "" if a == b else "   ← 分歧"
        print("  %-8s → %-8s  %3d 天%s" % (a, b, n, flag))

    # ★ 决策相关口径：消费点真正在意的是「defense/exit ⇒ 禁新建仓」这道硬闸
    BLOCK = ("defense", "exit")
    both_block = sum(n for (a, b), n in pairs.items() if a in BLOCK and b in BLOCK)
    ladder_stricter = sum(n for (a, b), n in pairs.items() if b in BLOCK and a not in BLOCK)
    ladder_looser = sum(n for (a, b), n in pairs.items() if a in BLOCK and b not in BLOCK)
    print("\n硬闸口（p2_entry_gate/wolf_253_build 真正看的那一刀：defense/exit ⇒ 禁新建仓）：")
    print("  两边都禁        ：%3d 天" % both_block)
    print("  **浪型链更严**（生产放行 → 浪型链禁）：%3d 天  ← 接管会多拦这些天" % ladder_stricter)
    print("  **浪型链更松**（生产禁 → 浪型链放行）：%3d 天  ← 接管会少拦这些天" % ladder_looser)

    if diverged:
        print("\n分歧样例（最近 %d 条）：" % min(args.limit, len(diverged)))
        for day, prod, lop, why in diverged[-args.limit:]:
            print("  %s  生产=%-8s 浪型链=%-8s | %s" % (day, prod, lop, why))
    print("\n接管影响：若 WOLF_WAVE_LADDER=1，%d/%d 天（%.1f%%）的 operation 会被改写" % (
        len(diverged), tot, 100.0 * len(diverged) / max(tot, 1)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
