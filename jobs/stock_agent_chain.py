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
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STEPS = (
    # 参数沿用原 16:55 任务的口径（--top 30 --advanced-top 10）✓
    ("P3 候选事实", "stock_agent_facts.py", ("--top", "30", "--advanced-top", "10")),
    ("P3 agent 选股", "stock_agent_select.py", ()),
    ("P3 选股→仓位", "stock_agent_position.py", ()),
)


def has_theme(date: str) -> bool:
    p = os.path.join(ROOT, "data", "mainline", date, "theme.json")
    if os.path.exists(p):
        return True
    try:
        import psycopg2
        with psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=3) as c:
            with c.cursor() as cur:
                cur.execute("select 1 from theme_snapshot where trade_date=%s", (date[:4] + "-" + date[4:6] + "-" + date[6:],))
                return cur.fetchone() is not None
    except Exception:
        return False


def wait_theme(date: str, timeout: int = 1800) -> bool:
    """L2 可能因上游 502 重试到 17:15；P3 不能只靠 16:55 文件。"""
    t0 = time.time()
    while not has_theme(date):
        if time.time() - t0 > timeout:
            print(f"[chain] wait theme timeout {timeout}s", flush=True)
            return False
        print("[chain] waiting theme.json / theme_snapshot ...", flush=True)
        time.sleep(30)
    return True


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
    if not wait_theme(a.date):
        print("[chain] P3 前置未就绪：theme.json/theme_snapshot 缺失", flush=True)
        return 1
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
