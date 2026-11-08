#!/usr/bin/env python3
"""lint_dsn.py — 硬编码 DSN 棘轮：连接串只能从 core/dsn.py 取

背景：
    收口前全仓有 163 处硬编码 DSN（`postgresql://user:pass@host:port/db`），
    端口散在三个库上（18789 线上隧道 / 5432 本地 / 5433 回测），口令进了 git。
    2026-11-08 收口后只剩 2 处，都在 apps/main_line/db_dsn.py —— 它本身就是
    「多候选探活」的解析器，那两个字面量是候选清单，不能替换掉。

规则：
    基线只能降不能升。新增一处硬编码 DSN 就让检查失败，必须改成
    `from core import dsn; dsn.main()` / `dsn.backtest()`。

    TEST-NET 文档地址（192.0.2.0/24、198.51.100.0/24、203.0.113.0/24）不计入：
    那些是测试用的占位地址，不是真实配置。

用法：
    .venv/bin/python scripts/lint_dsn.py
    .venv/bin/python scripts/lint_dsn.py --update-baseline [--force --note '原因']
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, ".dsn-baseline.json")
SCAN_DIRS = ["jobs", "scripts", "apps", "core", "backend"]
SKIP_PARTS = {"__pycache__", "node_modules", ".venv", ".git", "tests", "fixtures", ".dsh-tmp",
              "data", "logs", "dist", "openspec"}

DSN = re.compile(r"""["']postgres(?:ql)?://[^"'\s]+["']""")
TESTNET = re.compile(r"@(192\.0\.2\.|198\.51\.100\.|203\.0\.113\.)")


def count_sites() -> dict[str, int]:
    per: dict[str, int] = {}
    for d in SCAN_DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for dp, dn, fn in os.walk(base):
            dn[:] = [x for x in dn if x not in SKIP_PARTS]
            for f in fn:
                if not f.endswith(".py") or f.startswith("test_"):
                    continue
                full = os.path.join(dp, f)
                rel = os.path.relpath(full, ROOT)
                if any(p in SKIP_PARTS for p in rel.split(os.sep)):
                    continue
                try:
                    s = open(full, encoding="utf-8-sig", errors="ignore").read()
                except OSError:
                    continue
                n = sum(1 for m in DSN.finditer(s) if not TESTNET.search(m.group(0)))
                if n:
                    per[rel] = n
    return per


def load_baseline() -> dict:
    if not os.path.exists(BASELINE):
        return {"total": 0, "files": {}, "note": "尚未建立基线"}
    try:
        return json.load(open(BASELINE, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"total": 0, "files": {}, "note": "基线损坏，按 0 处理"}


def main() -> int:
    ap = argparse.ArgumentParser(description="硬编码 DSN 棘轮")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--force", action="store_true", help="允许升高基线（需 --note 说明）")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    cur = count_sites()
    cur_total = sum(cur.values())
    base = load_baseline()
    base_files: dict[str, int] = base.get("files") or {}
    base_total = int(base.get("total") or 0)

    if args.update_baseline:
        if os.path.exists(BASELINE) and cur_total > base_total and not args.force:
            print(f"拒绝：当前 {cur_total} > 基线 {base_total}（棘轮只能降）。", file=sys.stderr)
            return 1
        json.dump({"total": cur_total, "files": dict(sorted(cur.items())),
                   "note": args.note or base.get("note", "")},
                  open(BASELINE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"ok: 基线已更新 {base_total} → {cur_total}")
        return 0

    bad = [f"  {p}: {base_files.get(p, 0)} → {n}" for p, n in sorted(cur.items())
           if n > base_files.get(p, 0)]
    if bad:
        print("[dsn] ✗ 出现新的硬编码连接串：", file=sys.stderr)
        print("\n".join(bad), file=sys.stderr)
        print("\n[dsn] 改法：from core import dsn; conn = psycopg2.connect(dsn.main())", file=sys.stderr)
        print(f"[dsn] 当前 {cur_total} / 基线 {base_total}", file=sys.stderr)
        return 1

    print(f"[dsn] ✓ 无新增。当前 {cur_total} / 基线 {base_total}（唯一允许持有字面量的是 core/dsn.py 与本模块的解析器）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
