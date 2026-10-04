# -*- coding: utf-8 -*-
"""scan_silent_excepts.py —— 扫出**静默吞异常**的地方（账本 §9.543，用户「避免吞异常」）

**为什么**：今天三次翻车**全是静默吞异常**（`%` 转义异常⇒闸门失效、路径错⇒臂库读不到、
目录不存在⇒状态不写），而它们都藏在 `except …: pass` 后面。
输出：按文件聚合的清单（含行号与上下文），供逐个改成 `alert_hub.note()` 或至少 print。
用法：`.venv/bin/python jobs/scan_silent_excepts.py [--top 20] [--dir backend/app/services]`
"""
from __future__ import annotations
import os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAT = re.compile(r"except[^:]*:\s*\n\s*(pass|continue)\s*(#.*)?$", re.M)


def main() -> int:
    top, scan = 20, ["backend/app/services", "apps/main_line", "jobs"]
    for i, a in enumerate(sys.argv):
        if a == "--top" and i + 1 < len(sys.argv): top = int(sys.argv[i + 1])
        if a == "--dir" and i + 1 < len(sys.argv): scan = [sys.argv[i + 1]]
    hits = []
    for d in scan:
        base = os.path.join(ROOT, d)
        for dirpath, _dirs, files in os.walk(base):
            if "_bt_code_year" in dirpath or "__pycache__" in dirpath:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    s = open(p, encoding="utf-8").read()
                except Exception:
                    continue
                n = len(PAT.findall(s))
                if n:
                    hits.append((n, os.path.relpath(p, ROOT)))
    hits.sort(reverse=True)
    tot = sum(h[0] for h in hits)
    print("  ── 静默吞异常清单（`except …: pass/continue` ✓）──")
    for n, p in hits[:top]:
        print("    %4d 处  %s" % (n, p))
    print("  ⇒ 合计 %d 处，涉及 %d 个文件 ✓（全量：去掉 --top 限制）" % (tot, len(hits)))
    print("  ⇒ 建议：**影响决策/状态/I-O 的**优先改（监控、网关、状态文件、取数）✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
