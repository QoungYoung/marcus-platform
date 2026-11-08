#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""stock_agent_review.py — P3 选股 agent 结果校验（只读/落地候选）。

校验：
  - theme_id 必须在 facts 里；
  - symbol 必须在 candidates；
  - classification/verdict 合法；每主题 <=3；不重复；
  - confidence 0-1，evidence/counter_evidence 为数组。
"""
from __future__ import annotations
import argparse, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED_CLS = {"核心", "风向标", "龙头", "龙2", "二供", "杂毛"}
ALLOWED_VERDICT = {"probe", "watch", "reject"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20261023")
    ap.add_argument("--review", default="")
    a = ap.parse_args()
    base = os.path.join(ROOT, "data", "mainline", a.date)
    facts = json.load(open(os.path.join(base, "stock_agent_facts.json"), encoding="utf-8"))
    review = json.load(open(a.review or os.path.join(base, "stock_agent_review.json"), encoding="utf-8"))
    themes = {t["theme_id"]: t for t in facts.get("themes", [])}
    errs, warns, rows = [], [], []
    for item in review:
        tid = str(item.get("theme_id"))
        if tid not in themes:
            errs.append(f"{tid}: 不在 facts"); continue
        cand = {x["symbol"]: x for x in themes[tid].get("candidates", [])}
        sel = item.get("selected") or []
        if len(sel) > 3:
            errs.append(f"{tid}: selected >3")
        seen = set()
        for x in sel:
            sym = str(x.get("symbol"))
            if sym not in cand:
                errs.append(f"{tid}: {sym} 不在候选")
            if sym in seen:
                errs.append(f"{tid}: {sym} 重复")
            seen.add(sym)
            if x.get("classification") not in ALLOWED_CLS:
                errs.append(f"{tid}: {sym} classification 非法 {x.get('classification')}")
            if x.get("verdict") not in ALLOWED_VERDICT:
                errs.append(f"{tid}: {sym} verdict 非法 {x.get('verdict')}")
            try:
                c = float(x.get("confidence"))
                if not (0 <= c <= 1):
                    errs.append(f"{tid}: {sym} confidence 越界")
            except Exception:
                errs.append(f"{tid}: {sym} confidence 非数值")
            if not isinstance(x.get("evidence"), list) or not isinstance(x.get("counter_evidence"), list):
                errs.append(f"{tid}: {sym} evidence/counter 非数组")
        if not any(x.get("verdict") == "reject" for x in sel):
            warns.append(f"{tid}: 没有 reject 说明杂毛")
        rows.append((tid, len(sel), item.get("needs_human")))
    print(f"P3选股结果 {a.date}")
    for tid, n, nh in rows:
        bad = any(e.startswith(tid + ":") for e in errs)
        print(f"  主题 {tid}: 选中{n}只, 需要人工={'是' if nh else '否'}, 校验={'失败' if bad else '通过'}")
    if warns:
        print("提示:"); [print("  -", w) for w in warns]
    if errs:
        print("错误:"); [print("  -", e) for e in errs]
    print(f"结论: {'通过' if not errs else '失败'}; 错误{len(errs)}条, 提示{len(warns)}条")
    print(f"明细: stock_agent_selected.json / stock_agent_validated.json")
    json.dump({"date": a.date, "review": review, "errors": errs, "warnings": warns},
              open(os.path.join(base, "stock_agent_validated.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump({"date": a.date, "themes": review},
              open(os.path.join(base, "stock_agent_selected.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    if errs:
        sys.exit(1)


if __name__ == "__main__":
    main()
