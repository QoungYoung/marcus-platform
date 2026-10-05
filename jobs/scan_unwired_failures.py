# -*- coding: utf-8 -*-
"""scan_unwired_failures.py —— **"失败路径没接告警"的覆盖率审计**（账本 §9.623 ✓）

背景（用户 2026-10-05 质问：「不是说好全局异常做完了吗，怎么这么多没接的」✓）：
  · `scan_silent_excepts.py` 只治了 **`except …: pass/continue`** 这一类（"静默吞"✗）
  · 但真正让人看不见的是 **"失败即降级"** ✗：
      ① `except` 里只 `print`／只 log，**没 note** ✗
      ② `if rc != 0` / `if not ok` / `if 失败` 之类**分支只 print** ✗
    ⇒ 这类**看起来在跑**（有日志 ✓）但**没人被通知** ✗ —— 今天连着踩了三次（假警报 ✗／
      `候选现算 rc=1` ✗／`sqlite 打不开` ✗）

本脚本用 **AST** 扫出这些点，按文件聚合输出 ✓（只读 ✓，不改代码 ✓）。
用法：`.venv/bin/python jobs/scan_unwired_failures.py [--top N] [--files ...]`
"""
from __future__ import annotations
import argparse
import ast
import os
import sys

NOTE_HINTS = ("note(", "_silent_alert(", "alert_hub", "push_qq", "note_silent(")
PRINT_HINTS = ("print(", "log", "warn", "warning", "flush=True")
# 失败分支的判据词 ✓
FAIL_HINTS = ("rc", "returncode", "not ok", "failed", "失败", "!= 0", "!=0", " not ", "is None")


def _has(node, hints) -> bool:
    for n in ast.walk(node):
        src = ""
        if isinstance(n, ast.Call):
            try:
                src = ast.unparse(n.func)
            except Exception:
                src = ""
        elif isinstance(n, ast.Name):
            src = n.id
        elif isinstance(n, ast.Attribute):
            try:
                src = ast.unparse(n)
            except Exception:
                src = ""
        for h in hints:
            if h.strip("(") in src:
                return True
    return False


def scan_file(path: str):
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception as _e_scan:
        print("[scan_unwired] 跳过（解析失败）%s: %s" % (path, str(_e_scan)[:70]), flush=True)
        return []
    out = []
    for node in ast.walk(tree):
        # ① except 块：有 print/log 但**无 note** ⇒ 未接线 ✗
        if isinstance(node, ast.ExceptHandler):
            body = node.body
            has_note = any(_has(ast.Module(body=[s], type_ignores=[]), NOTE_HINTS) for s in body)
            has_print = any(_has(ast.Module(body=[s], type_ignores=[]), PRINT_HINTS) for s in body)
            if has_print and not has_note:
                out.append((node.lineno, "except", ast.unparse(node)[:90].replace("\n", " ")))
        # ② if 失败分支：判据词 + 有 print 但无 note ⇒ 未接线 ✗
        if isinstance(node, ast.If):
            try:
                test = ast.unparse(node.test)
            except Exception:
                continue
            if any(h in test for h in FAIL_HINTS):
                seg = ast.Module(body=node.body, type_ignores=[])
                if _has(seg, PRINT_HINTS) and not _has(seg, NOTE_HINTS):
                    out.append((node.lineno, "if失败分支", ("if %s: …" % test)[:90]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=18)
    ap.add_argument("--files", nargs="*", default=None)
    a = ap.parse_args()
    roots = a.files or ["backend/app", "jobs", "apps/main_line", "core"]
    agg, total = {}, 0
    for root in roots:
        if not os.path.exists(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dirpath, fn)
                hits = scan_file(p)
                if hits:
                    agg[p] = hits
                    total += len(hits)
    print("── 未接告警的**失败路径**（AST 扫描 ✓）──")
    print("   判据 ✓：① `except` 里有 print/log 但**无 note/alert** ✗"
          "　② `if 失败/rc≠0/…` 分支里只有 print ✗")
    for p, hits in sorted(agg.items(), key=lambda kv: -len(kv[1]))[: a.top]:
        print("   %-46s %3d 处" % (p, len(hits)))
        for ln, kind, txt in hits[:3]:
            print("        L%-6d %-8s %s" % (ln, kind, txt))
    print("   ── 合计 ✓: **%d 处**／%d 个文件 ✓" % (total, len(agg)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
