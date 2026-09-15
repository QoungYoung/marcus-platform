# -*- coding: utf-8 -*-
"""check_corpus_coverage.py — 语料覆盖度体检：**派生 md 语料 vs 原始 xlsx**（2026-09-15 round 14）。

背景：买入层参数总账（§1–§20）与全部抽取产物都建立在 `docs/wolf-daily-log-*.md`
（= 容器 `/app/data/corpus/`）之上。round 14 发现这些 md 是**派生导出**，
而权威源是仓库根目录的 `狼大回复汇总 20260814-1457&往期.xlsx`（8 sheet / 5 万余单元格）。
本脚本量化"派生 md 到底覆盖了原表的多少"，并**给出可复现的口径**。

口径（重要，两次踩坑后定的）：
  * 只统计**他本人**的发言：排除含 `[quote]` 的楼层（那是引用别人的帖子）；
  * 只统计**交易相关**：正文含 买/卖/仓/止损/低吸/挂/板块/方向/低点/做T/加仓/减仓/止盈/均线/量能/资金 任一关键词；
  * 长度 ≥24 字（短句/表情/水帖没有判据价值）；
  * **覆盖判据 = 该帖的任意 20 字连续片段（步长 8）能否在 md 语料里找到**。
    ⚠️ 不要用"取前 24 字前缀"（长帖在 md 里常只摘中段 → 会严重低估覆盖）；
    ⚠️ 也不要用"n-gram 建表 + 同步长查询"（若建表用 stride 2、查询也用 stride 2，**奇偶错位会全判未命中**）。

用法::

    .venv/bin/python jobs/check_corpus_coverage.py                      # md vs 仓库根 xlsx
    .venv/bin/python jobs/check_corpus_coverage.py --xlsx <path> --corpus docs
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jobs"))

from verify_buy_quotes import _files, load_corpus, norm_punct  # noqa: E402

KW = ("买", "卖", "仓", "止损", "低吸", "挂", "板块", "方向", "低点", "做T", "加仓", "减仓",
      "止盈", "均线", "量能", "资金")
DEFAULT_XLSX = os.path.join(ROOT, "狼大回复汇总 20260814-1457&往期.xlsx")
OUT = os.path.join(ROOT, ".dsh-tmp", "buyside", "corpus_coverage.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", default=DEFAULT_XLSX)
    ap.add_argument("--corpus", default=os.path.join(ROOT, "docs"))
    ap.add_argument("--win", type=int, default=20)
    ap.add_argument("--stride", type=int, default=8)
    ap.add_argument("--json", default=OUT)
    args = ap.parse_args()

    corp = load_corpus(_files(args.corpus))
    md = "".join(t[1] for t in corp.values())
    if not md:
        print("[coverage] 没载入 md 语料")
        return 2
    if not os.path.exists(args.xlsx):
        print("[coverage] 找不到 xlsx：%s" % args.xlsx)
        return 2
    import pandas as pd
    xl = pd.ExcelFile(args.xlsx)

    stat, ex = {}, []
    for sh in xl.sheet_names:
        df = xl.parse(sh, header=None, dtype=str)
        n = c = 0
        for col in df.columns:
            for v in df[col].dropna().astype(str):
                if "[quote]" in v or len(v) < 24 or not any(k in v for k in KW):
                    continue
                n += 1
                t = norm_punct(v)
                hit = any(t[i:i + args.win] in md for i in range(0, max(1, len(t) - args.win + 1), args.stride))
                c += hit
                if not hit and len(ex) < 10:
                    ex.append({"sheet": sh, "post": v[:120].replace("\n", " ")})
        stat[sh] = {"trade_posts": n, "covered": c}
    N = sum(v["trade_posts"] for v in stat.values())
    C = sum(v["covered"] for v in stat.values())
    print("[coverage] md 语料 %d 字 | xlsx sheet %d 个" % (len(md), len(xl.sheet_names)))
    print("[coverage] 他本人、非引用、交易相关、≥24 字：**%d 条**；md 覆盖 **%d 条（%.1f%%）**"
          % (N, C, 100.0 * C / max(N, 1)))
    for sh in xl.sheet_names:
        v = stat[sh]
        print("   %-24s %5d → %5d (%.0f%%)" % (sh, v["trade_posts"], v["covered"],
                                               100.0 * v["covered"] / max(v["trade_posts"], 1)))
    print("[coverage] 未覆盖示例：")
    for e in ex[:6]:
        print("   [%s] %s" % (e["sheet"], e["post"][:90]))
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    json.dump({"md_chars": len(md), "trade_posts": N, "covered": C, "by_sheet": stat, "uncovered_examples": ex},
              open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[coverage] 写出 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
