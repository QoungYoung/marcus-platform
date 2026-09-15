# -*- coding: utf-8 -*-
"""scan_exec_thresholds.py — 执行/成交侧阈值 × 语料检索（2026-09-15 round 31，总账 §40）。

针对总账 §19 里「影响成交/风险敞口」的那一档键（此前 92 条"策略阈值"未逐条判定），
按语义分组检索他的原话：每日买几只 / 做T价差门槛 / 回补(补回) / 手续费与滑点 /
每日亏损与冷却 / 仓位纪律 / 量能门槛 / 分批买法。只做**定位**，判读靠逐条精读。

语料 = 仓库根 xlsx（权威源），**不手打**。

用法::

    # 本地（仓库根那份 xlsx）
    WOLF_XLSX='狼大回复汇总 20260814-1457&往期.xlsx' .venv/bin/python jobs/scan_exec_thresholds.py
    # 只统计条数、不打印正文（快速看有没有料）
    WOLF_XLSX='...' .venv/bin/python jobs/scan_exec_thresholds.py --counts
    # 只看 2025-01 起的"当前时代"
    WOLF_XLSX='...' .venv/bin/python jobs/scan_exec_thresholds.py --since 2025-01-01
"""
import argparse
import os
import re

import pandas as pd

XLSX = os.getenv("WOLF_XLSX", "/app/狼大回复汇总 20260814-1457&往期.xlsx")

GROUPS = [
    ("① 每日/一次买几只（MAX_DAILY_BUY_LEGS=2 / WOLF_PICK_MAX_LEGS=4）",
     re.compile(r"(一天|每日|当天|一次).{0,12}(买|做|开|挂).{0,12}([1-9一二三四五六七八九十]\s*[只个条]|几[只个条])")),
    ("①b 同主题/持仓只数",
     re.compile(r"(同(一)?(个)?板块|一个主题|同题材|持有|持仓|手上|手里).{0,25}?([0-9一二三四五六七八九十]{1,2}\s*[-~到]?\s*[0-9一二三四五六七八九十]?\s*[只个支])")),
    ("② 做T价差门槛（MIN_T_SPREAD=0.5% / MIN_T_SPREAD_FILTER=0.2%）",
     re.compile(r"(做T|做t|T出|T回|高抛低吸|正T|反T|接回|补回).{0,50}?([0-9]+(\.[0-9]+)?\s*(个点|%|％|块|毛))")),
    ("③ 回补（WOLF_REFILL_DAYS=2 / CHASE_MAX=1% / MAX_WINDOW=2 / MAX_AGE=10）",
     re.compile(r"(补回|回补|接回|买回|再买回)")),
    ("③b 回补反向条件（不追高）",
     re.compile(r"(不补|不追|不回补|没必要补|不着急补|别补|不要追)")),
    ("④ 手续费/滑点（FEE_PCT=0.001 / SLIPPAGE=0.0003）",
     re.compile(r"(手续费|佣金|印花税|滑点|交易成本|费率)")),
    ("⑤ 每日亏损与冷却（COST_RATIO_LIMIT=0.2 / DAILY_LOSS_WARN=1% / COOLDOWN=15min）",
     re.compile(r"(当天亏|今日亏|亏损超过|亏了.{0,6}(停|不做|收手)|\d+\s*分钟.{0,6}(不做|停))")),
    ("⑥ 加仓档距/仓位档（每跌N%补 / 点位→仓位）",
     re.compile(r"((跌|回撤|下来).{0,10}?([0-9]+(\.[0-9]+)?)\s*(个点|%|％).{0,14}?(补|加|买|接))|"
                r"(([0-9]{4}).{0,10}?(跌破|没站上|站稳|企稳).{0,14}?[0-9]{2}\s*%)")),
    ("⑦ 量能档位（地量2WE / 突破3WE）",
     re.compile(r"(1WE|1\.5WE|2WE|3WE|1we|2we|3we|一万亿|两万亿|三万亿)")),
    ("⑧ 分批买法（分三批/越跌越买）",
     re.compile(r"(分\s*[0-9一二三四五六七八九十]\s*批|分三次|分两次|分批|越跌越买|慢慢买)")),
]


def load_posts(since=""):
    xl = pd.ExcelFile(XLSX)
    out = []
    for sh in xl.sheet_names:
        df = xl.parse(sh, header=None, dtype=str)
        for _, r in df.iterrows():
            m = re.match(r"(\d{4}-\d{2}-\d{2})", str(r.get(0) or ""))
            d8 = m.group(1) if m else ""
            if since and d8 and d8 < since:
                continue
            for c in list(df.columns)[1:]:
                v = r.get(c)
                if isinstance(v, str) and len(v.strip()) >= 12 and "[quote]" not in v:
                    out.append((d8, v.strip()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="", help="只看该日期之后（如 2025-01-01）")
    ap.add_argument("--limit", type=int, default=10, help="每组打印条数")
    ap.add_argument("--counts", action="store_true", help="只打印命中条数")
    args = ap.parse_args()

    posts = load_posts(args.since)
    print("xlsx =", XLSX)
    print("原帖（不含引用块%s）: %d" % ("，since " + args.since if args.since else "", len(posts)))
    for name, rx in GROUPS:
        hits = sorted((d, t) for d, t in posts if rx.search(t))
        print("\n===== %s：%d 条 =====" % (name, len(hits)))
        if args.counts:
            continue
        for d, t in hits[: args.limit]:
            print("· [%s] %s" % (d or "?", t[:220].replace("\n", " ⏎ ")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
