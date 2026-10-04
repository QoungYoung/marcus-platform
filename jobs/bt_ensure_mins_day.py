# -*- coding: utf-8 -*-
"""bt_ensure_mins_day.py —— 当日「缺分钟⇒自动补齐（含退避重试）」钩子（账本 §9.470 ✓ 用户要求 ✓）

**为什么需要** ✓（§9.468/§9.469 实证 ✓）：
  · 臂每天会新布腿 ⇒ 新标的的分钟可能缺 ✗ ⇒ 盘中判断会被**静默跳过** ✗（"离场层哑火"同源 ✓）
  · 而**缺了自己补**会遇到两个坑 ✓：
    ① **没加载 `.env`** ⇒ `datahubco` 403 / `promax` 像"没数据" ✗（实测 ✓）
    ② `503 minute_data_pending` ＝ **上游正在后台补数** ✓ ⇒ **要退避重试** ✓（不是"没有"✗）
  本脚本把这两件事都做掉 ✓：**自动读 `.env` 的 key** ✓ ＋ **最多 N 轮退避重试** ✓

**开关** ✓：`WOLF_ENSURE_MINS`（**默认 1 = 开** ✓，用户 2026-10-03 要求；置 0 关 ✓）
用法 ✓：`.venv/bin/python jobs/bt_ensure_mins_day.py --day 20260310 [--root data/_bt_t35 --account drabt35]`
"""
from __future__ import annotations

import argparse, os, subprocess, sys, time


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def enabled() -> bool:
    return str(os.getenv("WOLF_ENSURE_MINS", "1")).strip().lower() in ("1", "true", "yes", "on")


def load_env_keys() -> int:
    """把 `.env` 里**缺失**的变量补进本进程 ✓（只读 `.env` ✓；已存在的**不覆盖** ✓）。"""
    p = os.path.join(REPO, ".env")
    if not os.path.exists(p):
        return 0
    n = 0
    try:
        for ln in open(p, encoding="utf-8", errors="replace"):
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
                n += 1
    except Exception as _e_sil1:
        _silent_alert("bt_ensure_mins_day.py:42", _e_sil1)
    return n


def run_backfill(day: str, root: str, account: str, check_only: bool) -> str:
    cmd = [sys.executable, os.path.join(REPO, "jobs", "bt_backfill_mins_union.py"),
           "--start", day, "--end", day, "--root", root, "--account", account]
    if check_only:
        cmd.append("--check-only")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=REPO)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return "ERR:%s" % str(e)[:80]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--root", default=os.path.join("data", "_bt_t35"))
    ap.add_argument("--account", default=os.getenv("T_MONITOR_ACCOUNT", "drabt35") or "drabt35")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--sleep", type=float, default=20.0)
    a = ap.parse_args()
    if not enabled():
        print("  [ensure_mins] 关（WOLF_ENSURE_MINS=0）⇒ 跳过 ✓")
        return 0
    nk = load_env_keys()
    print("  [ensure_mins] %s ✓｜从 .env 补入 %d 个变量 ✓（key 存在=%s ✓）"
          % (a.day, nk, bool(os.getenv("PROMAX_API_KEY"))), flush=True)
    for i in range(1, max(1, a.rounds) + 1):
        out = run_backfill(a.day, a.root, a.account, check_only=(i > 1))
        miss = [l for l in out.splitlines() if "MISS" in l]
        tail = [l for l in out.splitlines() if l.startswith("[bf]")][-1:] or [""]
        print("  [ensure_mins] 第 %d 轮 ✓ %s" % (i, tail[0][:110]), flush=True)
        if not miss and i > 1:
            print("  [ensure_mins] ✅ 无缺口 ✓", flush=True)
            return 0
        if i < a.rounds:
            time.sleep(a.sleep)
    print("  [ensure_mins] ⚠️ 仍有缺口 %d 个（多为上游 minute_data_pending ⇒ 下次运行会再补 ✓）" % len(miss), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
