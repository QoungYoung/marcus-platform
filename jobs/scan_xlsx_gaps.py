# -*- coding: utf-8 -*-
"""scan_xlsx_gaps.py — 原始 xlsx 里"md 未覆盖"的那 62% 有多少**新的买入侧判据**（2026-09-15 round 15）。

背景：round 14 实测 `docs/wolf-daily-log-*.md`（= 容器 `/app/data/corpus/`）只覆盖原表
`狼大回复汇总 20260814-1457&往期.xlsx` 里他本人交易相关发言的 **38.1%**（总账 §22）。
在做"全量重抽"之前先花小钱回答：**缺的那部分里，到底有没有我们账上没有的买入判据？**

做法（纯离线、不用 LLM）：
  1. 取 xlsx 里**他本人**（排除含 `[quote]` 的楼层）、**交易相关**（买卖/仓/止损/低吸/挂/板块/方向/低点/做T/加仓/减仓/止盈/均线/量能/资金）、≥24 字的帖子；
  2. 用 20 字滑窗判据标出** md 未覆盖**的子集；
  3. 在这些未覆盖帖里，按**买入侧关键词**（买/低吸/建仓/加仓/补/抄底/埋伏/挂/上车/介入/买点）筛句，
     并抽出**带数字的短句**（数字+单位：个点/%/％/天/日/成/倍/WE/亿/元/线）；
  4. 与既有抽取产物 `wolf_buy_params_evidence.json`（md 侧的 1,127 条带数字判据）做**滑窗比对**，
     标出哪些是**新的**（既有产物里找不到）→ 这就是"缺的 62% 里的增量"。

用法::

    .venv/bin/python jobs/scan_xlsx_gaps.py
    .venv/bin/python jobs/scan_xlsx_gaps.py --limit 30 --json .dsh-tmp/buyside/xlsx_gap_scan.json
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jobs"))

from verify_buy_quotes import _files, load_corpus, norm_punct  # noqa: E402

DEFAULT_XLSX = os.path.join(ROOT, "狼大回复汇总 20260814-1457&往期.xlsx")
EVIDENCE = os.path.join(ROOT, ".dsh-tmp", "buyside", "wolf_buy_params_evidence.json")
OUT = os.path.join(ROOT, ".dsh-tmp", "buyside", "xlsx_gap_scan.json")

TRADE_KW = ("买", "卖", "仓", "止损", "低吸", "挂", "板块", "方向", "低点", "做T", "加仓", "减仓",
            "止盈", "均线", "量能", "资金")
BUY_KW = ("买", "低吸", "建仓", "加仓", "补", "抄底", "埋伏", "挂", "上车", "介入", "买点", "吸")
NUM_UNIT = re.compile(r"\d+(?:\.\d+)?\s*(?:个点|个板|成|倍|天|日|周|月|%|％|WE|we|亿|万|元|线|点)")
WIN = 20


def sentences(text):
    return [s.strip() for s in re.split(r"[。！？!?；;\n]+", text) if len(s.strip()) >= 8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", default=DEFAULT_XLSX)
    ap.add_argument("--corpus", default=os.path.join(ROOT, "docs"))
    ap.add_argument("--evidence", default=EVIDENCE)
    ap.add_argument("--limit", type=int, default=40, help="最多打印多少条增量判据")
    ap.add_argument("--json", default=OUT)
    args = ap.parse_args()

    corp = load_corpus(_files(args.corpus))
    md = "".join(t[1] for t in corp.values())
    ev = []
    try:
        ev = (json.load(open(args.evidence, encoding="utf-8")).get("items") or [])
    except Exception as e:
        print("[gap] 读不到既有抽取产物：%s（增量比对会退化为'全部算新'）" % str(e)[:60])
    ev_text = norm_punct(" ".join((it.get("quote") or "") for it in ev))

    import pandas as pd
    xl = pd.ExcelFile(args.xlsx)
    n_posts = n_uncov = n_buy_sent = n_new = 0
    new_items, by_sheet = [], {}
    for sh in xl.sheet_names:
        df = xl.parse(sh, header=None, dtype=str)
        st = by_sheet.setdefault(sh, {"posts": 0, "uncovered": 0, "buy_sent": 0, "new": 0})
        # 表结构：第 0 列=发帖时间，第 1 列=回复内容（2026-09-15 实测；其余列若有也一并扫）
        rows = []
        for _, r in df.iterrows():
            d8 = ""
            m = re.match(r"(\d{4}-\d{2}-\d{2})", str(r.get(0) or ""))
            if m:
                d8 = m.group(1)
            for c in list(df.columns)[1:]:
                v = r.get(c)
                if isinstance(v, str) and v.strip():
                    rows.append((d8, v))
        for d8, v in rows:
                if "[quote]" in v or len(v) < 24 or not any(k in v for k in TRADE_KW):
                    continue
                n_posts += 1
                st["posts"] += 1
                t = norm_punct(v)
                covered = any(t[i:i + WIN] in md for i in range(0, max(1, len(t) - WIN + 1), 8))
                if covered:
                    continue
                n_uncov += 1
                st["uncovered"] += 1
                for sent in sentences(v):
                    if not any(k in sent for k in BUY_KW) or not NUM_UNIT.search(sent):
                        continue
                    n_buy_sent += 1
                    st["buy_sent"] += 1
                    ns = norm_punct(sent)
                    # 与既有产物比对：句子的任一 16 字窗口命中既有 quote → 不算新
                    hit = any(ns[i:i + 16] in ev_text for i in range(0, max(1, len(ns) - 15), 4))
                    if not hit:
                        n_new += 1
                        st["new"] += 1
                        if len(new_items) < 4000:
                            new_items.append({"sheet": sh, "date": d8, "sentence": sent[:200]})
    print("[gap] xlsx 交易相关帖 %d → md 未覆盖 %d（%.0f%%）" % (n_posts, n_uncov, 100.0 * n_uncov / max(n_posts, 1)))
    print("[gap] 未覆盖帖里**买入侧且带数字**的句子 %d 条 → 其中**既有产物里没有的（增量）%d 条**" % (n_buy_sent, n_new))
    for sh in xl.sheet_names:
        v = by_sheet[sh]
        print("   %-24s 帖 %5d / 未覆盖 %5d / 买入数字句 %4d / **新增 %4d**"
              % (sh, v["posts"], v["uncovered"], v["buy_sent"], v["new"]))
    print("\n[gap] 增量样例（前 %d 条）：" % min(args.limit, len(new_items)))
    for it in new_items[:args.limit]:
        print("   [%s %s] %s" % (it["sheet"], it.get("date") or "?", it["sentence"][:120]))
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    json.dump({"posts": n_posts, "uncovered": n_uncov, "buy_numeric_sentences": n_buy_sent,
               "new_vs_existing_evidence": n_new, "by_sheet": by_sheet, "new_items": new_items},
              open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n[gap] 写出 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
