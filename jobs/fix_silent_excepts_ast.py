# -*- coding: utf-8 -*-
"""fix_silent_excepts_ast.py —— **AST 版**静默点改造（账本 §9.544，用户「做」）

为什么换 AST（正则版的两次教训 ✓）：
  · 单行正则**截断多行 `except (\\n A, B\\n):`** ⇒ 曾把 3 个文件写坏 ✗（虽已回滚 ✓）
  · 正则也吃不动 `except …:` 换行 `continue` 的形式 ✗

本版覆盖三类（都用 **AST 行号**精确定位 ✓，不做文本正则 ✓）：
  ① body == [Pass]          ⇒ **替换**为留痕语句 ✓
  ② body[0] == Continue     ⇒ 在它**前面插入**留痕语句 ✓（保留 continue ✓）
  ③ 多行 except 头          ⇒ 由 AST 天然支持 ✓（此时不捕获异常变量，改用 msg ✓）

硬门槛（今天的教训 ✓）：**先 `compile()` 通过，才允许写盘** ✓。关键文件走 `alert_hub.note_silent` ✓，其余走 `print` ✓。
用法：`.venv/bin/python jobs/fix_silent_excepts_ast.py [--apply] [--files a.py,b.py]`
"""
from __future__ import annotations
import ast, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRITICAL = ("t_monitor.py", "t_gateway.py", "stop_loss_monitor.py", "position_tier_monitor.py",
            "position_tier.py", "wolf_discipline.py", "t_base_floor.py", "tranche_ladder.py",
            "alert_hub.py", "daily_decision.py", "scheduler_service.py")


def _indent_of(lines, lineno: int) -> str:
    ln = lines[lineno - 1]
    return ln[: len(ln) - len(ln.lstrip())]


def _note_lines(ind: str, tag: str, use_hub: bool, excvar: str = "") -> list:
    arg = excvar if excvar else "None"
    tail = (', %s' % arg) if excvar else ''
    if use_hub:
        return [ind + "try:",
                ind + "    from app.services import alert_hub as _ah_sil",
                ind + '    _ah_sil.note_silent("%s"%s)' % (tag, tail),
                ind + "except Exception:",
                ind + '    print("[silent:%s] %%s: %%s" %% (type(%s).__name__, str(%s)[:110]), flush=True)'
                      % (tag, excvar or "Exception()", excvar or "Exception()")]
    return [ind + 'print("[silent:%s] swallow" %s, flush=True)' % (tag, '')]


def convert(path: str, apply: bool) -> tuple:
    src = open(path, encoding="utf-8").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return (0, "原文语法错误，跳过")
    lines = src.split("\n")
    name = os.path.basename(path)
    use_hub = name in CRITICAL
    edits = []          # (lineno, end_lineno, action, ind, excvar)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body = node.body or []
        if not body:
            continue
        # ⚠️ 护栏 1：**一行式** `except X: pass`（头与体同一行）⇒ 跳过（双重编辑会写坏 ✗）
        if body[0].lineno == node.lineno:
            continue
        if len(body) == 1 and isinstance(body[0], ast.Pass):
            edits.append((body[0].lineno, body[0].end_lineno, "replace", _indent_of(lines, body[0].lineno), ""))
        elif body and isinstance(body[0], ast.Continue):
            edits.append((body[0].lineno, body[0].lineno, "insert_before",
                          _indent_of(lines, body[0].lineno), ""))
    if not edits:
        return (0, "无可改")
    # 单行 except ⇒ 补 `as _e_silN` 便于捕获异常对象；多行头 ⇒ 不动（用 msg）
    # ⚠️ 护栏 2：只在**单行头**且**体在其后**时才补 `as _e_silN`（多行头不动 ✗）
    handler_hdr = {h.lineno: h for h in ast.walk(tree) if isinstance(h, ast.ExceptHandler)}
    for i, (l0, l1, act, ind, _ev) in enumerate(edits, 1):
        hdr = None
        for hl, hn in handler_hdr.items():
            if hl < l0 and hl == max([x for x in handler_hdr if x < l0] or [0]):
                hdr = hn
                break
        if hdr is None:
            continue
        h = lines[hdr.lineno - 1]
        if (h.rstrip().endswith(":") and " as " not in h and h.strip().startswith("except")
                and hdr.body and hdr.body[0].lineno > hdr.lineno):
            lines[hdr.lineno - 1] = h[: h.rfind(":")] + " as _e_sil%d:" % i
            edits[i - 1] = (l0, l1, act, ind, "_e_sil%d" % i)
    # 自下而上替换，避免行号漂移
    for i, (l0, l1, act, ind, ev) in reversed(list(enumerate(edits, 1))):
        tag = "%s:%d" % (name, l0)
        note = _note_lines(ind, tag, use_hub, ev)
        if act == "replace":
            lines[l0 - 1:l1] = note
        else:
            lines[l0 - 1:l0 - 1] = note
    out = "\n".join(lines)
    try:
        compile(out, path, "exec")          # ★ 硬门槛：编译不过 ⇒ 不写盘
    except SyntaxError as e:
        return (0, "转换后语法错误，未写盘（%s）" % str(e)[:60])
    if apply:
        open(path, "w", encoding="utf-8").write(out)
    return (len(edits), "已写盘 ✓" if apply else "演练 ✓")


def main() -> int:
    apply = "--apply" in sys.argv
    files = []
    for i, a in enumerate(sys.argv):
        if a == "--files" and i + 1 < len(sys.argv):
            files = [f.strip() for f in sys.argv[i + 1].split(",")]
    if not files:
        print("  用法：--files a.py,b.py [--apply]"); return 0
    tot = 0
    print("  ── AST 版静默点改造（%s）──" % ("落盘 ✓" if apply else "演练 ✓"))
    for p in files:
        if not os.path.exists(p):
            print("    %-44s 不存在 ✗" % os.path.basename(p)); continue
        n, note = convert(p, apply)
        tot += n
        print("    %-44s 改 %3d 处（%s）" % (os.path.basename(p), n, note))
    print("  ⇒ 共 %d 处 ✓" % tot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
