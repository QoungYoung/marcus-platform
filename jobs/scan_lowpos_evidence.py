# -*- coding: utf-8 -*-
"""scan_lowpos_evidence.py — 待定项 #1「低位最多跌 2 个点」的语料复核（2026-09-15 round 30）。

总账 §39。回答"这句话里『2 个点』相对什么"，顺便查他对「低位」的口径。
本脚本只做**定位**（把原帖连发帖时间取出来），**不做正则结构化**；判读靠逐条精读。

语料 = 仓库根 xlsx（权威源）`狼大回复汇总 20260814-1457&往期.xlsx`，**不手打**。
容器内可直接跑（默认路径 /app/…），本地用环境变量指到仓库根那份。

用法::

    WOLF_XLSX='狼大回复汇总 20260814-1457&往期.xlsx' .venv/bin/python jobs/scan_lowpos_evidence.py
    WOLF_XLSX='狼大回复汇总 20260814-1457&往期.xlsx' .venv/bin/python jobs/scan_lowpos_evidence.py --full
"""
import argparse
import os
import re
import sys

import pandas as pd

XLSX = os.getenv("WOLF_XLSX", "/app/狼大回复汇总 20260814-1457&往期.xlsx")

# ① 原话定位（要**含引用块**：那句本身回答了别人提问，正文里带 [quote]）
QUOTE_RX = re.compile(r"低位最多|跌了也就最多跌|最多也就跌\d个点")
# ② 「低位」口径
LOWPOS_DEF = [
    ("低位+前低/箱体/横盘/平台/底部", re.compile(r"低位.{0,20}(前低|箱体|横盘|平台|底部)")),
    ("低位+涨幅/位置", re.compile(r"低位.{0,20}(涨幅|位置|没涨|还没涨|不高的)")),
    ("定义式", re.compile(r"(低位就是|什么叫低位|低位指的是|低位的定义|低位的意思是)")),
    ("低位+跌得少", re.compile(r"低位.{0,30}(跌得少|跌也跌得|抗跌|不怎么跌|不跌)")),
]
# ③ 「跌 N 个点」的全部用法（判断是"描述"还是"规则"）
DROP_RX = re.compile(r"跌(了|个|破)?\s*(1|一|2|两|3|三)\s*个?点")


def load_cells(with_quote=True, min_len=8):
    xl = pd.ExcelFile(XLSX)
    out = []
    for sh in xl.sheet_names:
        df = xl.parse(sh, header=None, dtype=str)
        for _, r in df.iterrows():
            m = re.match(r"(\d{4}-\d{2}-\d{2})", str(r.get(0) or ""))
            d8 = m.group(1) if m else ""
            for c in list(df.columns)[1:]:
                v = r.get(c)
                if not isinstance(v, str) or len(v.strip()) < min_len:
                    continue
                if not with_quote and "[quote]" in v:
                    continue
                out.append((d8, v.strip()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="打印全部命中（默认只打印样例）")
    args = ap.parse_args()

    allc = load_cells(with_quote=True)
    posts = [(d, t) for d, t in allc if "[quote]" not in t]
    log = lambda *a: print(*a, flush=True)
    log("xlsx =", XLSX)
    log("文本单元 %d（其中不含引用块的原帖 %d）" % (len(allc), len(posts)))

    log("\n===== ① 原话定位（含引用块）=====")
    for d, t in allc:
        if QUOTE_RX.search(t):
            log("· [%s] %s" % (d or "?", t[:400].replace("\n", " ⏎ ")))

    log("\n===== ② 「低位」口径（不含引用块）=====")
    for name, rx in LOWPOS_DEF:
        hits = [(d, t) for d, t in posts if rx.search(t)]
        log("--- %s：%d 条 ---" % (name, len(hits)))
        for d, t in sorted(hits)[: (999 if args.full else 12)]:
            log("   · [%s] %s" % (d or "?", t[:200].replace("\n", " ⏎ ")))

    log("\n===== ③ 「跌 N 个点」全部用法（不含引用块）=====")
    hits = [(d, t) for d, t in posts if DROP_RX.search(t)]
    log("命中 %d 条" % len(hits))
    for d, t in sorted(hits)[: (999 if args.full else 20)]:
        log("   · [%s] %s" % (d or "?", t[:200].replace("\n", " ⏎ ")))
    log("\n判读（总账 §39）：『跌了也就最多跌 2 个点』是他给**防御类品种**下的定义（2025-10-23），"
        "2026-04-15 那句同样是在**解释为什么不怕调整**（持仓特征），全表无一处把它写成买点阈值。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
