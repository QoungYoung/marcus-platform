#!/usr/bin/env python3
"""lint_persist.py — 持久化棘轮：禁止新增裸 json.dump 状态落盘

背景（为什么要这个检查）：
    这个仓库历史上习惯把状态写进 data/*.json，而不是线上 PG。原因不是偏好，
    而是 PG 的入口长期是坏的（隧道断着），json.dump 又只要 1 行且落盘立刻可见。
    结果：435 处 json.dump 分散在 340 个文件里，每次新功能都会多加几处。

    一次性全改是不现实的，所以用**棘轮**：
      基线数字只能降，不能升。新增一处就让检查失败，必须要么改用 PG，
      要么显式跑 --update-baseline 承认增加（会把理由写进基线文件）。

用法：
    python scripts/lint_persist.py              # 检查，超基线退出码 1
    python scripts/lint_persist.py --update-baseline   # 压低基线（只能降）
    python scripts/lint_persist.py --update-baseline --force  # 允许升高（慎用）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, ".persist-baseline.json")
SCAN_ROOTS = ["jobs", "backend/app", "apps"]
SKIP_PARTS = {"__pycache__", "node_modules", ".dsh-tmp", ".venv", "test", "tests", "fixtures"}
PATTERN = re.compile(r"json\.dump\(")


def count_sites() -> dict[str, int]:
    per: dict[str, int] = {}
    for root in SCAN_ROOTS:
        abs_root = os.path.join(ROOT, root)
        if not os.path.isdir(abs_root):
            continue
        for dirpath, dirnames, filenames in os.walk(abs_root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS]
            for name in filenames:
                if not name.endswith(".py") or name.startswith("test_"):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, ROOT)
                if any(part in SKIP_PARTS for part in rel.split(os.sep)):
                    continue
                try:
                    text = open(full, encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                n = len(PATTERN.findall(text))
                if n:
                    per[rel] = n
    return per


def load_baseline() -> dict:
    if not os.path.exists(BASELINE):
        return {"total": 0, "files": {}, "note": "尚未建立基线"}
    try:
        return json.load(open(BASELINE, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"total": 0, "files": {}, "note": "基线文件损坏，按 0 处理"}


def main() -> int:
    ap = argparse.ArgumentParser(description="裸 json.dump 棘轮检查")
    ap.add_argument("--update-baseline", action="store_true", help="把当前计数写回基线（默认只允许降低）")
    ap.add_argument("--force", action="store_true", help="配合 --update-baseline：允许升高基线")
    ap.add_argument("--note", default="", help="写进基线的说明，例如为什么增加")
    args = ap.parse_args()

    current = count_sites()
    cur_total = sum(current.values())
    base = load_baseline()
    base_files: dict[str, int] = base.get("files") or {}
    base_total = int(base.get("total") or 0)

    if args.update_baseline:
        if not os.path.exists(BASELINE):
            pass  # 首次建基线：允许直接写入当前值
        elif cur_total > base_total and not args.force:
            print(f"拒绝：当前 {cur_total} > 基线 {base_total}。子命令只能降；"
                  f"确实要增加请加 --force 并写 --note。", file=sys.stderr)
            return 1
        payload = {
            "total": cur_total,
            "files": dict(sorted(current.items())),
            "note": args.note or base.get("note", ""),
        }
        json.dump(payload, open(BASELINE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"ok: 基线已更新 {base_total} → {cur_total}（{len(current)} 个文件）")
        return 0

    regressions: list[str] = []
    for path, n in sorted(current.items()):
        b = base_files.get(path, 0)
        if n > b:
            delta = n - b
            regressions.append(f"  {path}: {b} → {n}（+{delta}）")
    removed = sum(1 for p, b in base_files.items() if current.get(p, 0) < b)

    if regressions:
        print("[persist] ✗ 有新增的裸 json.dump 状态落盘：", file=sys.stderr)
        print("\n".join(regressions), file=sys.stderr)
        print(f"\n[persist] 当前 {cur_total} / 基线 {base_total}", file=sys.stderr)
        print("[persist] 改法：写线上 PG（见 NOW.md §2），临时中间产物写 .dsh-tmp/；"
              "确实要增加须显式 --update-baseline --force --note '原因'", file=sys.stderr)
        return 1

    print(f"[persist] ✓ 无新增。当前 {cur_total} / 基线 {base_total}"
          f"（{len(current)} 个文件；本次有 {removed} 个文件降到基线以下，可以 --update-baseline 压数字）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
