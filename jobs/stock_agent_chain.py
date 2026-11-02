#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""stock_agent_chain.py — P3 三步一条链：facts → select → position。

## 为什么合成一条链（2026-11-03）
三步原来是**独立调度**（16:55 facts / 17:15 select / 17:25 position），靠「等文件」串起来：
2026-11-02 下午有人反复改 `config/tasks.yaml` 并重启 worker（当日 9 次），16:55 那把 facts
**根本没触发** ⇒ 17:15 的 select 空等 15 分钟 rc=2 ⇒ 17:25 的 position 再空等 15 分钟 rc=2
（通知里只剩一串 `waiting for P3 …`）✗ —— 真正的责任方（facts 没跑）完全没暴露。

链化后：顺序强制、失败点落在真正失败的那一步；任一步失败即刻停并返回非零 ⇒ 通知一眼看出是哪步 ✗。

用法：
    python jobs/stock_agent_chain.py                 # 今天
    python jobs/stock_agent_chain.py --date 20261102  # 补某天
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STEPS = (
    # 参数沿用原 16:55 任务的口径（--top 30 --advanced-top 10）✓
    ("P3 候选事实", "stock_agent_facts.py", ("--top", "30", "--advanced-top", "10")),
    ("P3 agent 选股", "stock_agent_select.py", ()),
    ("P3 选股→仓位", "stock_agent_position.py", ()),
)


def run(script: str, date: str, extra=()) -> bool:
    p = os.path.join(ROOT, "jobs", script)
    cmd = [sys.executable, p, "--date", date, *extra]
    print("[chain] run", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print("[chain] FAIL", script, r.returncode, flush=True)
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    a = ap.parse_args()
    for i, (label, script, extra) in enumerate(STEPS, 1):
        print("[chain] ▶ %s（%d/%d）" % (label, i, len(STEPS)), flush=True)
        if not run(script, a.date, extra):
            print("[chain] 汇总: 第 %d/%d 步「%s」失败 ⇒ 后续不再跑 ✗" % (i, len(STEPS), label),
                  flush=True)
            return 1
    print("[chain] OK", a.date, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
