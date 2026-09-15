# -*- coding: utf-8 -*-
"""mine_selfset_params.py — 为总账 §4 的**自设参数**在 xlsx 全表里逐个找原话（2026-09-15 round 21）。

目的（objective ① 的最后一处缺口）：11 个参数标着「他完全没提、我们自造」，
但那是在 **md 语料（只覆盖原表 38%）** 上得出的结论。本轮用**原始 xlsx 全表**（含发帖时间）
按**每个参数的主题关键词**逐条检索，结论只有两种：
  · **有原话** → 升级：把他的话与数值贴回来（若与我们不同 → 改判"冲突/需改"）；
  · **无原话** → 记为「**xlsx 全表检索 0 命中**」——这才叫"确认自设"，比"我们没找到"强。

用法::

    .venv/bin/python jobs/mine_selfset_params.py            # 全部 11 个
    .venv/bin/python jobs/mine_selfset_params.py --id 1,10  # 只看某几个
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jobs"))
from verify_buy_quotes import norm_punct  # noqa: E402

DEFAULT_XLSX = os.path.join(ROOT, "狼大回复汇总 20260814-1457&往期.xlsx")
OUT = os.path.join(ROOT, ".dsh-tmp", "buyside", "selfset_param_mining.json")

# 每个自设参数：主题关键词（原话里出现这些词才算相关）+ **数值线索**（正则，命中即"可能有数值口径"）
TARGETS = {
  "1": {"name": "20日均成交额下限 1.0亿", "kw": ("成交额", "成交量", "量能", "小票", "成交"),
        "num": r"(\d+(?:\.\d+)?)\s*(?:亿|WE|we|万)"},
  "2": {"name": "位置闸 距前一日低≤5%", "kw": ("前一日低", "前一天低", "前低", "低点", "挂"),
        "num": r"(\d+(?:\.\d+)?)\s*(?:个点|%|％|点)"},
  "3": {"name": "tier2 接近档 ≤8%", "kw": ("接近", "差", "位置", "低点"), "num": r"(\d+(?:\.\d+)?)\s*(?:个点|%|％)"},
  "4": {"name": "涨停判定 9.7%", "kw": ("涨停", "板"), "num": r"(\d+(?:\.\d+)?)\s*(?:个点|%|％|板)"},
  "5": {"name": "leader 三因子等权 1/3", "kw": ("龙头", "辨识度", "排序", "选"), "num": r"(\d+(?:\.\d+)?)"},
  "6": {"name": "风向标死 -0.5%", "kw": ("风向标", "龙头死", "破位", "跌破"), "num": r"(\d+(?:\.\d+)?)\s*(?:个点|%|％)"},
  "7": {"name": "路径A 市值预筛 80 只", "kw": ("市值", "盘子", "自选", "候选"), "num": r"(\d+)\s*(?:只|个|支)"},
  "8": {"name": "每主题布腿数 4", "kw": ("方向", "只", "个票", "买几"), "num": r"(\d+)\s*(?:个方向|只|个票)"},
  "9": {"name": "删票 TTL 13 交易日", "kw": ("删票", "不看", "不看了"), "num": r"(\d+)\s*(?:天|日|周)"},
  "10": {"name": "253 上证5min跌幅≥0.4%", "kw": ("急杀", "跳水", "跌"), "num": r"(\d+(?:\.\d+)?)\s*(?:个点|%|％)"},
  "11": {"name": "254 缩量上限 0.9", "kw": ("缩量", "地量"), "num": r"(\d+(?:\.\d+)?)\s*(?:倍|成|WE|we)"},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", default=DEFAULT_XLSX)
    ap.add_argument("--id", default="", help="逗号分隔的参数编号（默认全部）")
    ap.add_argument("--limit", type=int, default=6, help="每个参数打印多少条带数值的命中")
    ap.add_argument("--json", default=OUT)
    args = ap.parse_args()

    want = [x.strip() for x in args.id.split(",") if x.strip()] or list(TARGETS)
    import pandas as pd
    xl = pd.ExcelFile(args.xlsx)
    posts = []
    for sh in xl.sheet_names:
        df = xl.parse(sh, header=None, dtype=str)
        for _, r in df.iterrows():
            m = re.match(r"(\d{4}-\d{2}-\d{2})", str(r.get(0) or ""))
            d8 = m.group(1) if m else ""
            for c in list(df.columns)[1:]:
                v = r.get(c)
                if isinstance(v, str) and len(v.strip()) >= 16 and "[quote]" not in v:
                    posts.append({"sheet": sh, "date": d8, "text": v})
    print("[mine] xlsx 帖子 %d 条" % len(posts))

    res = {}
    for pid in want:
        t = TARGETS.get(pid)
        if not t:
            continue
        hits, numhits = [], []
        for p in posts:
            for sent in re.split(r"[。！？!?；;\n]+", p["text"]):
                s = sent.strip()
                if len(s) < 8 or not any(k in s for k in t["kw"]):
                    continue
                hits.append({"date": p["date"], "s": s[:200]})
                if re.search(t["num"], s):
                    numhits.append({"date": p["date"], "s": s[:200]})
        res[pid] = {"name": t["name"], "n_hits": len(hits), "n_num": len(numhits), "num_examples": numhits[:12]}
        print("\n===== #%s %s =====" % (pid, t["name"]))
        print("   相关句 %d 条，其中**带数值** %d 条" % (len(hits), len(numhits)))
        for h in numhits[:args.limit]:
            print("     [%s] %s" % (h["date"] or "?", h["s"][:130]))
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    json.dump(res, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n[mine] 写出 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
