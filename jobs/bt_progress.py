#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bt_progress.py — 查看**本机全年回测**进度（一条命令搞定）。

用法（在仓库根目录）：
    .venv/bin/python jobs/bt_progress.py
    .venv/bin/python jobs/bt_progress.py --root data/_bt_year --log /tmp/local_year.log

输出：已跑/总天数、当前正在跑的交易日、腿数（逐日 + 合计）、速度与预计剩余时间、进程与两条隧道状态。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_env  # noqa: E402


def trade_days_total(root: str, bars_db: str, log: str = ""):
    """目标天数：优先取日志首行"`N 个交易日`"（跑批窗口），否则用库里的交易日总数。"""
    if log and os.path.exists(log):
        import re
        for ln in open(log, encoding="utf-8", errors="replace"):
            m = re.search(r"(\d+)\s*个交易日", ln)
            if m:
                return int(m.group(1))
    import sqlite3
    c = sqlite3.connect(bars_db)
    r = c.execute("SELECT count(DISTINCT trade_date) FROM bars").fetchone()
    c.close()
    return int(r[0] or 0)


def done_days(root: str):
    """已建沙箱的交易日（= seed 已跑完/在跑）+ 其腿数。"""
    out = {}
    for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        p = os.path.join(root, d)
        if not (len(d) == 8 and d.isdigit() and os.path.isdir(p)):
            continue
        # 只认"seed 跑完"的天（有 _seed.json）；半成品目录不算进度
        if not os.path.exists(os.path.join(p, "_seed.json")):
            continue
        n = 0
        for fn in ("legs_switch.jsonl", "legs.jsonl"):
            f = os.path.join(p, fn)
            if os.path.exists(f):
                n += sum(1 for ln in open(f, encoding="utf-8") if ln.strip())
        out[d] = n
    return out


def legs_detail(root: str, day: str):
    syms = []
    for fn in ("legs_switch.jsonl", "legs.jsonl"):
        f = os.path.join(root, day, fn)
        if not os.path.exists(f):
            continue
        for ln in open(f, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                j = json.loads(ln)
                syms.append(j.get("symbol"))
            except Exception:
                pass
    return syms


def procs():
    try:
        o = subprocess.run(["ps", "-eo", "pid,etime,cmd"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return [], []
    run, tun = [], []
    for ln in o.splitlines():
        if "bt_days.py" in ln and "grep" not in ln:
            run.append(ln.strip())
        if ("local_pg_serve.py" in ln or "local_llm.py" in ln) and "grep" not in ln:
            tun.append(ln.strip())
    return run, tun


def port_open(port: int):
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


# ⚠️ 2026-09-16 说明：本脚本读的是**沙箱目录 + /tmp/local_year.log**——那是「布腿重放 + bt_account
#    内存账户」那条旧链的产物。生产链年跑（bt_days --prod → jobs/bt_prod_run.py，本地 PG）的结果
#    落在 `<root>/_summary/prod_<day>.json`，请用 `jobs/bt_prod_progress.py`。
#    本脚本保留只作交叉校验；数字会包含旧链遗留的腿文件，**不能**当成新跑进度。
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(bt_env.DATA, "_bt_year"))
    ap.add_argument("--log", default="/tmp/local_year.log")
    ap.add_argument("--bars-db", default=os.path.join(bt_env.DATA, "_bt_full", "bars.sqlite"))
    a = ap.parse_args()

    if not os.path.isdir(a.root):
        print("还没有跑批目录：%s" % a.root)
        return 1

    days = done_days(a.root)
    total = trade_days_total(a.root, a.bars_db, a.log)
    n_done = len(days)
    n_legs = sum(days.values())
    print("== 全年回测进度（%s）" % a.root)
    print("   已处理 %d / %d 个交易日   腿合计 %d 条" % (n_done, total, n_legs))

    # 速度 / ETA（用日志里每个 seed 的耗时或文件 mtime 估）
    log_lines = []
    if os.path.exists(a.log):
        log_lines = [ln for ln in open(a.log, encoding="utf-8", errors="replace") if "seed rc=" in ln or "腿" in ln]
    if n_done >= 2:
        try:
            mt = sorted(os.path.getmtime(os.path.join(a.root, d)) for d in days)
            span = max(mt[-1] - mt[0], 1.0)
            per = span / (n_done - 1)
            left = per * max(total - n_done, 0)
            print("   速度 ≈ %.0f 秒/天（按沙箱目录时间估算）   预计剩余 ≈ %.1f 小时" % (per, left / 3600))
        except Exception:
            pass

    run, tun = procs()
    cur = ""
    if run:
        cur = "运行中（%s）" % run[0].split()[1]
    print("   进程：%s" % (cur or "**没有在跑**（可能已结束或被中断）"))
    print("   隧道：PG %s / LLM %s" % (
        "✅" if port_open(15432) else "❌", "✅" if port_open(13001) else "❌"))

    withlegs = {d: n for d, n in days.items() if n}
    print("\n== 有腿的交易日（%d 天）" % len(withlegs))
    for d in sorted(withlegs):
        print("   %s  %d 条  %s" % (d, withlegs[d], " ".join(x for x in legs_detail(a.root, d) if x)))
    if not withlegs:
        print("   （暂无：这些天策略当天没有合格候选）")

    print("\n== 最近 8 个已处理交易日")
    for d in sorted(days)[-8:]:
        print("   %s  %d 条" % (d, days[d]))

    if os.path.exists(a.log):
        print("\n== 日志尾部（%s）" % a.log)
        for ln in open(a.log, encoding="utf-8", errors="replace").read().splitlines()[-5:]:
            print("   " + ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
