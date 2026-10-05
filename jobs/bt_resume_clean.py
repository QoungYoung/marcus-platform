# -*- coding: utf-8 -*-
"""bt_resume_clean.py —— **续跑前的"已完成"标记清理**（账本 §9.589 ✓，用户指出 ✓）

**为什么需要它** ✗：
`bt_days` 的续跑逻辑看到 `_summary/prod_<日>.json` 存在 ⇒ **认为该日已完成 ⇒ 跳过** ✗。
于是出现这种危险情形 ✓：
  1. 账户被重置（清仓、清成交 ✓）
  2. 但该日的 `prod_<日>.json` **还是上一轮的** ✗
  ⇒ ⇒ **成交没了、日也不重跑 ⇒ 静默丢一天** ✗（2026-10-05 实测发生过 ✓）

**本脚本**：删除 **`>= RESUME_FROM`** 的所有 `prod_*.json` ✓（只清"要重跑的那段" ✓），并打印明细 ✓。

用法 ✓：
  `.venv/bin/python jobs/bt_resume_clean.py --root data/_bt_t35d --from 20260105`
  `.venv/bin/python jobs/bt_resume_clean.py --root data/_bt_t35d --from 20260105 --dry-run`
"""
from __future__ import annotations
import argparse
import glob
import os
import sys


def clean(root: str, from_day: str, dry_run: bool = False) -> int:
    summ = os.path.join(root, "_summary")
    files = sorted(glob.glob(os.path.join(summ, "prod_*.json")))
    hit, keep = [], 0
    for f in files:
        d = os.path.basename(f)[5:13]
        if d and d >= str(from_day):
            hit.append((d, f))
        else:
            keep += 1
    print("  ── 续跑清理 ✓（root=%s，from=%s）──" % (root, from_day))
    print("     现有 summary ✓: %d 个（保留 %d 个 < %s ✓）" % (len(files), keep, from_day))
    for d, f in hit:
        if dry_run:
            print("     [dry-run] 将删除 ✓ %s" % d)
        else:
            try:
                os.remove(f)
                print("     已删除 ✓ %s（旧 summary ✗）" % d)
            except Exception as e:
                print("     ✗ 删除 %s 失败: %s" % (d, str(e)[:60]))
    print("     ⇒ 共%s %d 个 ✓" % ("将删除" if dry_run else "已删除", len(hit)))
    return len(hit)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--from", dest="d0", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not os.path.isdir(a.root):
        print("  ✗ root 不存在: %s" % a.root); return 2
    n = clean(a.root, a.d0, a.dry_run)
    return 0 if n >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
