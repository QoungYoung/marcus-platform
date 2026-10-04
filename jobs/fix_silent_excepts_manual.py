# -*- coding: utf-8 -*-
"""fix_silent_excepts_manual.py —— **人工小批**静默点改造（账本 §9.545；把 daily_decision 那次的流程固化）

覆盖：`except …:` 后紧跟 `pass` 或 `continue` 的（含多行 except ✓），在文件内注入一个 `_silent_alert` 出口并逐点调用。

**三次踩坑都已修（这是本版存在的理由 ✓）**：
  ① **自下而上**改（自上而下会因插入行而漂移行号 ✗）
  ② 已有 `as name` 的**复用**变量名（否则出行 `as a as b` ✗）
  ③ **先 `compile()` 再写盘**（不通过 ⇒ 跳过，绝不写坏 ✓）
并把多行 except 头**原样保留** ✓（只改 body ✓）。

用法：`.venv/bin/python jobs/fix_silent_excepts_manual.py --file <path> [--apply]`
"""
from __future__ import annotations
import ast, os, re, sys

HELPER = '''

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
'''


def convert(path: str, apply: bool) -> int:
    name = os.path.basename(path)
    lines = open(path, encoding="utf-8").read().split("\n")
    # 找 except 头（可能多行 ⇒ 用括号配平找到真正的 ':' 行）
    sites = []            # (hdr_first_lineno, hdr_last_lineno, body_lineno, body_kind, indent)
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)except\b", lines[i])
        if m:
            ind = m.group(1)
            depth, j = 0, i
            while j < len(lines):
                depth += lines[j].count("(") - lines[j].count(")")
                if depth <= 0 and lines[j].rstrip().endswith(":"):
                    break
                j += 1
            bl = j + 1
            while bl < len(lines) and not lines[bl].strip():
                bl += 1
            if bl < len(lines) and lines[bl].strip() in ("pass", "continue"):
                sites.append((i, j, bl, lines[bl].strip(), ind))
                i = bl
        i += 1
    if not sites:
        print("    %-42s 无静默点 ✓" % name); return 0
    if "_silent_alert" not in "\n".join(lines):
        # ⚠️ 只在**缩进为 0** 的 import 之后注入（否则会插进 `try:` 块里 ✗ —— 今早 bt_days 同款事故）
        # ★★ 用 **AST** 求"最后一个**顶层** import 语句的**结束行**" ✓
        #   为什么不能用"以 from 开头的最后一行" ✗：多行 import（带括号）会停在**括号内** ✗
        #   —— 实测 market_reference.py 的 `from app.models.market_orm import (` 正是这样被写坏的 ✗
        try:
            _tree = ast.parse("\n".join(lines))
            _last = 0
            for _n in _tree.body:
                if isinstance(_n, (ast.Import, ast.ImportFrom)):
                    _last = max(_last, getattr(_n, "end_lineno", _n.lineno))
            last_imp = _last if _last else 0
        except Exception:
            last_imp = 0
        lines.insert(last_imp + 1, HELPER)
        sites = [(a + 1, b + 1, c + 1, d, e) for (a, b, c, d, e) in sites]
    for k, (h0, h1, bl, kind, ind) in reversed(list(enumerate(sites))):
        hdr = lines[h0]
        var = "_e_sil%d" % (k + 1)
        # ⚠️ 裸 `except:`（无类型）**不能**加 as（`except as x:` 非法 ✗）⇒ 保持原样、不捕获对象 ✓
        if h0 == h1 and re.match(r"^\s*except\s*:\s*(#.*)?$", hdr):
            var = "None"
        elif h0 == h1 and " as " not in hdr:               # 单行头 ⇒ 补 as（多行头不动 ✓）
            lines[h0] = hdr[: hdr.rfind(":")] + " as %s:" % var
        elif h0 == h1:
            mm = re.search(r"\bas\s+([A-Za-z_][A-Za-z_0-9]*)", hdr)
            var = mm.group(1) if mm else var
        else:
            var = "None"                                   # 多行头 ⇒ 不捕获异常对象
        call = '%s_silent_alert("%s:%d", %s)' % (ind + "    ", name, h0 + 1, var)
        if kind == "pass":
            lines[bl] = call
        else:
            lines[bl:bl] = [call]
    out = "\n".join(lines)
    try:
        compile(out, path, "exec")
    except SyntaxError as e:
        print("    %-42s 跳过：转换后语法错误（%s）⇒ 未写盘 ✓" % (name, str(e)[:60])); return 0
    if apply:
        open(path, "w", encoding="utf-8").write(out)
    print("    %-42s 改 %2d 处 ✓%s" % (name, len(sites), "（已写盘 ✓）" if apply else "（演练）"))
    return len(sites)


def main() -> int:
    apply = "--apply" in sys.argv
    files = []
    for i, a in enumerate(sys.argv):
        if a == "--file" and i + 1 < len(sys.argv): files.append(sys.argv[i + 1])
        if a == "--files" and i + 1 < len(sys.argv): files += [f.strip() for f in sys.argv[i + 1].split(",")]
    if not files:
        print("  用法：--file <path> [--apply]"); return 0
    tot = sum(convert(f, apply) for f in files if os.path.exists(f))
    print("  ⇒ 共 %d 处 ✓" % tot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
