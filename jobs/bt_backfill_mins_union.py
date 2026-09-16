# -*- coding: utf-8 -*-
"""bt_backfill_mins_union.py — 按**并集名单**补齐分钟缓存（修"离场层哑火"的根因）。

缺口成因：`bt_days --formal` 只对**当天布腿标的**拉分钟，而持仓票的离场腿（vwap/support/high_sell）
与当日 armed 条件标的都不在名单里 → 没有当日 bars → `TMonitor._round` 静默跳过 → 仓位冻结。
实测：588 个标的日中 196 个（33%）没有当日分钟，越靠后越糟（20260313 全缺）。

本脚本：逐日取**并集**（当天腿文件 ∪ 本地 PG 持仓 ∪ 该日 armed/expired 条件 ∪ 前一日产出 JSON 的 symbols），
对缺失的 (标的, 日) 调 `jobs/bt_fetch_mins.py` 补齐；跑完再复核一遍缺口。

用法：
  .venv/bin/python jobs/bt_backfill_mins_union.py --start 20260105 --end 20260914 \
      [--root data/_bt_year] [--account stock] [--limit N] [--check-only]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys

sys.path[:0] = []
import bt_env  # noqa: E402

MINS = os.path.join(bt_env.DATA, "_bt_full", "mins")


def _have(symbol: str, day: str) -> bool:
    code6, ex = symbol[2:8], symbol[:2]
    return os.path.exists(os.path.join(MINS, "%s_%s_5min_%s.json" % (code6, ex, day)))


def _legs_symbols(sb: str) -> set:
    out = set()
    for fn in ("legs_switch.jsonl", "legs.jsonl"):
        p = os.path.join(sb, fn)
        if not os.path.exists(p):
            continue
        for ln in open(p, encoding="utf-8"):
            ln = ln.strip()
            if ln:
                try:
                    out.add(json.loads(ln).get("symbol"))
                except Exception:
                    pass
    return {s for s in out if s}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20260105")
    ap.add_argument("--end", default="20260914")
    ap.add_argument("--root", default=os.path.join(bt_env.DATA, "_bt_year"))
    ap.add_argument("--account", default="stock")
    ap.add_argument("--limit", type=int, default=0, help="只补前 N 个 (标的,日)（试跑用）")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.0)
    a = ap.parse_args()

    import psycopg2
    conn = psycopg2.connect(os.environ.get("DATABASE_URL", "postgresql://marcus:marcus123@5433/marcus_trading")
                            .replace("127.0.0.1:5433", "127.0.0.1:5433"))
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT symbol FROM paper_positions WHERE account_id=%s AND volume>0", (a.account,))
    held = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT trade_date, symbol FROM t_conditions WHERE account_id=%s AND status IN ('active','expired')",
                (a.account,))
    cond = {}
    for d, s in cur.fetchall():
        cond.setdefault(str(d)[:8] if "-" not in str(d) else str(d).replace("-", "")[:8], set()).add(s)
    cur.close(); conn.close()
    print("[bf] 持仓并集 %d 只；条件覆盖 %d 天" % (len(held), len(cond)), flush=True)

    days = sorted(os.path.basename(p)[5:13] for p in glob.glob(os.path.join(a.root, "_summary", "prod_*.json")))
    days = [d for d in days if a.start <= d <= a.end]
    todo, total, have = [], 0, 0
    for d in days:
        sb = os.path.join(a.root, d)
        syms = _legs_symbols(sb) | held | cond.get(d, set())
        # 前一日产出 JSON 里的 symbols（生产链实际监控名单）
        pj = os.path.join(a.root, "_summary", "prod_%s.json" % d)
        if os.path.exists(pj):
            try:
                syms |= set((json.load(open(pj, encoding="utf-8")) or {}).get("symbols") or [])
            except Exception:
                pass
        for s in sorted(x for x in syms if x):
            total += 1
            if _have(s, d):
                have += 1
            else:
                todo.append((s, d))
    print("[bf] %d 天：标的日合计 %d，已有分钟 %d，缺 %d（%.0f%%）"
          % (len(days), total, have, len(todo), 100.0 * len(todo) / max(total, 1)), flush=True)
    if a.check_only:
        for s, d in todo[:20]:
            print("   MISS %s %s" % (s, d))
        return 0
    if a.limit:
        todo = todo[:a.limit]
    ok = 0
    for i, (s, d) in enumerate(todo, 1):
        ts = s[2:8] + "." + s[:2]
        r = subprocess.run([sys.executable, bt_env.jobs_file("bt_fetch_mins.py"), "--symbols", s,
                            "--days", d, "--freq", "5min", "--out", MINS, "--index", ""],
                           capture_output=True, text=True, timeout=600)
        got = _have(s, d)
        ok += 1 if got else 0
        print("[bf] %d/%d %s %s → %s" % (i, len(todo), s, d, "ok" if got else "FAIL"), flush=True)
        if a.sleep:
            import time as _t; _t.sleep(a.sleep)
    print("[bf] 完成：成功 %d / %d" % (ok, len(todo)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
