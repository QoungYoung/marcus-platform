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
    check = "--check" in sys.argv          # 账本 §9.548：防回潮模式（有静默点 ⇒ 退出码 1）
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
                # 白名单：alert_hub 自身必须用 pass ✗⇒✓（否则 note_silent 内部失败会再调自己 ⇒ 无限递归 ✗）
                if fn == "alert_hub.py":
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    s = open(p, encoding="utf-8").read()
                except Exception as _e_sil1:
                    # ★ 账本 §9.700 ✓：本文件**从未定义** _silent_alert ✗
                    #   ⇒ 真走这个分支就 NameError（把原始异常也吞掉 ✗）⇒ 换成自包含 print ✓
                    print("[scan-silent] 读文件失败 %s: %s"
                          % (p, str(_e_sil1)[:80]), flush=True)
                    continue
                # 排除我们自己注入的 `_silent_alert` helper 自带的 `except: pass`（否则每个改过的文件虚高 1 ✗）
                n = 0
                for m in PAT.finditer(s):
                    win = s[max(0, m.start() - 400):m.start()]
                    if "_ah.note_silent(" in win or "def _silent_alert(" in win:
                        continue
                    n += 1
                if n:
                    hits.append((n, os.path.relpath(p, ROOT)))
    hits.sort(reverse=True)
    tot = sum(h[0] for h in hits)
    print("  ── 静默吞异常清单（`except …: pass/continue` ✓）──")
    for n, p in hits[:top]:
        print("    %4d 处  %s" % (n, p))
    print("  ⇒ 合计 %d 处，涉及 %d 个文件 ✓（全量：去掉 --top 限制）" % (tot, len(hits)))
    print("  ⇒ 建议：**影响决策/状态/I-O 的**优先改（监控、网关、状态文件、取数）✓")
    if check and tot > 0:
        print("  ✗ 防回潮：出现 %d 处静默吞异常 ⇒ 请改成留痕（print / alert_hub.note_silent）" % tot)
        return 1
    if check:
        print("  ✓ 防回潮：全仓 **0 处**静默吞异常 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
