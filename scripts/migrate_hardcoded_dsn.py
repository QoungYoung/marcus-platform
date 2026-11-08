#!/usr/bin/env python3
"""migrate_hardcoded_dsn.py — 把硬编码 DSN 收口到 core/dsn.py

用法：
    .venv/bin/python scripts/migrate_hardcoded_dsn.py            # 只报告（默认）
    .venv/bin/python scripts/migrate_hardcoded_dsn.py --apply    # 真正改
    .venv/bin/python scripts/migrate_hardcoded_dsn.py --apply --only scripts/dip_buy_deep.py

设计原则（为什么不是一句 sed）：
    · 只替换**整行形状明确**的用法，遇到拿不准的一律跳过并报告；
    · 每个改完的文件都用 compile() 校验（ast.parse 不查 __future__ 位置），不过就不写盘；
    · 端口决定语义：18789/5432 → dsn.main()；5433 → dsn.backtest()；
    · 指向公网 IP 的（81.70.44.68）不自动改——那不是"本地配置"问题，
      需要人工判断是否该直连生产库。

替换的形状：
    R1  os.getenv("X", "<dsn>") / os.environ.get("X", "<dsn>")   → dsn.main()/backtest()
    R2  NAME = "<dsn>"                                           → NAME = dsn.main()/backtest()
    R3  default="<dsn>"                                          → default=dsn.main()/backtest()
    R4  connect("<dsn>") / create_engine("<dsn>")                → connect(dsn...) / create_engine(dsn...)
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = ["jobs", "scripts", "apps", "core", "backend/app"]
SKIP_PARTS = {"__pycache__", "node_modules", ".venv", ".git", "tests", "fixtures", ".dsh-tmp"}

# 这些文件自己就是 DSN 策略的定义处 —— 批量替换会改坏语义，必须人工处理
EXCLUDE_REL = {
    "apps/main_line/db_dsn.py",   # 4 级探活兜底（env / .env / 5433 / docker 主机名）
    "backend/app/config.py",      # 后端权威配置（本次手改）
    "core/dsn.py",                # 新的唯一来源本身
    "core/qq_notifier.py",        # 已有 env 优先级逻辑（2026-09-26 事故后修的）
}

DSN_LIT = r"""["']postgres(?:ql)?://[^"'\s]+["']"""
RE_DSN_ANY = re.compile(DSN_LIT)
RE_GETENV = re.compile(
    r"""[A-Za-z_]\w*\.(?:getenv|environ\.get)\(\s*["'][A-Za-z_][\w]*["']\s*,\s*""" + DSN_LIT + r"""\s*\)"""
)
RE_ASSIGN = re.compile(r"""^(\s*)([A-Za-z_][\w]*)\s*=\s*""" + DSN_LIT + r"""\s*$""")
RE_DEFAULT = re.compile(r"""default\s*=\s*""" + DSN_LIT)
RE_CALLARG = re.compile(r"""((?:connect|create_engine)\()\s*""" + DSN_LIT + r"""(\s*[,)])""")
RE_OR_DEFAULT = re.compile(
    r"""[A-Za-z_]\w*\.(?:getenv|environ\.get)\(\s*["'][A-Za-z_][\w]*["']\s*\)\s*or\s*""" + DSN_LIT
)
RE_ALONE = re.compile(r"""^(\s*)""" + DSN_LIT + r"""((?:\s*[,)\]}]|\s*$).*)$""")
RE_ORLINE = re.compile(r"""^(\s*or\s+)""" + DSN_LIT + r"""((?:\s*[,)\]}]|\s*$).*)$""")
RE_TUPLE = re.compile(
    r"""^(\s*\(\s*["'][A-Za-z_][\w]*["']\s*,\s*)""" + DSN_LIT + r"""((?:\s*[,)\]}]|\s*$).*)$"""
)
RE_SETDEFAULT = re.compile(
    r"""os\.environ\.setdefault\(\s*["'][A-Za-z_][\w]*["']\s*,\s*""" + DSN_LIT
)

PUBLIC_MARKERS = ("81.70.44.68",)

RE_VARNAME = re.compile(r"""(?:getenv|environ\.get|setdefault)\(\s*["']([A-Za-z_][\w]*)["']""")


def var_name(text: str) -> str:
    m = RE_VARNAME.search(text)
    return m.group(1) if m else ""


def kind_from_var(name: str) -> str | None:
    """环境变量名比端口更可信：BT_* 就是回测库，其余按主库。"""
    if not name:
        return None
    u = name.upper()
    if u.startswith("BT") or "_BT_" in u or u.endswith("_BT"):
        return "backtest"
    return "main"


def kind_of(literal: str) -> str | None:
    """按端口判语义；返回 None 表示不该自动改。"""
    if any(m in literal for m in PUBLIC_MARKERS):
        return None
    m = re.search(r":(\d{4,5})/", literal)
    if not m:
        return None
    port = m.group(1)
    if port == "5433":
        return "backtest"
    if port in ("5432", "18789"):
        return "main"
    return None


def needs_bootstrap(src: str) -> bool:
    return "sys.path" not in src


def has_dsn_import(src: str) -> bool:
    return bool(re.search(r"^\s*(from\s+core\s+import\s+.*\bdsn\b|from\s+core\.dsn\s+import|import\s+core\.dsn)", src, re.M))


def depth_of(rel: str) -> int:
    """从文件所在目录回到仓库根需要上几级。"""
    parts = rel.split(os.sep)[:-1]          # 去掉文件名
    return len(parts) + 1                    # +1：dirname 一次只到文件所在目录，再到仓库根


def build_import_block(depth: int) -> str:
    expr = "_os_dsn.path.abspath(__file__)"
    for _ in range(depth):                       # 嵌套调用，不能写成逗号分隔的元组
        expr = f"_os_dsn.path.dirname({expr})"
    return (
        "\n# ── DSN 收口（scripts/migrate_hardcoded_dsn.py 自动插入，见 core/dsn.py）──\n"
        "import os as _os_dsn, sys as _sys_dsn\n"
        f"_ROOT_DSN = {expr}\n"
        "if _ROOT_DSN not in _sys_dsn.path:\n"
        "    _sys_dsn.path.insert(0, _ROOT_DSN)\n"
        "from core import dsn as _dsn_src  # noqa: E402\n"
    )


def transform(src: str) -> tuple[str, int, list[str]]:
    changes = 0
    skipped: list[str] = []
    out_lines: list[str] = []

    for line in src.splitlines(keepends=True):
        if not RE_DSN_ANY.search(line):
            out_lines.append(line)
            continue
        original = line

        # R1: getenv(..., "<dsn>")
        def _r1(m: re.Match) -> str:
            nonlocal changes
            k = kind_from_var(var_name(m.group(0))) or kind_of(m.group(0))
            if not k:
                return m.group(0)
            changes += 1
            return f"_dsn_src.{k}()"

        line = RE_GETENV.sub(_r1, line)

        # R5: os.getenv("X") or "<dsn>"
        def _r5(m: re.Match) -> str:
            nonlocal changes
            k = kind_from_var(var_name(m.group(0))) or kind_of(m.group(0))
            if not k:
                return m.group(0)
            changes += 1
            return f"_dsn_src.{k}()"

        line = RE_OR_DEFAULT.sub(_r5, line)

        # R7: os.environ.setdefault("X", "<dsn>")
        def _r7(m: re.Match) -> str:
            nonlocal changes
            k = kind_from_var(var_name(m.group(0))) or kind_of(m.group(0))
            if not k:
                return m.group(0)
            changes += 1
            return m.group(0).split(",")[0] + f", _dsn_src.{k}()"

        line = RE_SETDEFAULT.sub(_r7, line)

        # R6: 整行就是一个 DSN 字面量（跨行 connect 的实参）
        m6 = RE_ALONE.match(line.rstrip("\n"))
        if m6 and RE_DSN_ANY.search(line):
            k = kind_of(RE_DSN_ANY.search(line).group(0))
            if k:
                line = f"{m6.group(1)}_dsn_src.{k}(){m6.group(2)}\n"
                changes += 1

        # R8: 续行 `or "<dsn>"`（getenv 在上一行）
        if RE_DSN_ANY.search(line):
            m8 = RE_ORLINE.match(line.rstrip("\n"))
            if m8:
                k = kind_of(RE_DSN_ANY.search(line).group(0))
                if k:
                    line = f"{m8.group(1)}_dsn_src.{k}(){m8.group(2)}\n"
                    changes += 1

        # R9: ("VAR", "<dsn>") 元组形式
        if RE_DSN_ANY.search(line):
            m9 = RE_TUPLE.match(line.rstrip("\n"))
            if m9:
                k = kind_of(RE_DSN_ANY.search(line).group(0))
                if k:
                    line = f"{m9.group(1)}_dsn_src.{k}(){m9.group(2)}\n"
                    changes += 1

        # R2: NAME = "<dsn>"
        m = RE_ASSIGN.match(line.rstrip("\n"))
        if m and RE_DSN_ANY.search(line):
            k = kind_of(RE_DSN_ANY.search(line).group(0))
            if k:
                line = f"{m.group(1)}{m.group(2)} = _dsn_src.{k}()\n"
                changes += 1

        # R3: default="<dsn>"
        def _r3(m: re.Match) -> str:
            nonlocal changes
            k = kind_of(m.group(0))
            if not k:
                return m.group(0)
            changes += 1
            return f"default=_dsn_src.{k}()"

        line = RE_DEFAULT.sub(_r3, line)

        # R4: connect("<dsn>")
        def _r4(m: re.Match) -> str:
            nonlocal changes
            k = kind_of(m.group(0))
            if not k:
                return m.group(0)
            changes += 1
            return f"{m.group(1)}_dsn_src.{k}(){m.group(2)}"

        line = RE_CALLARG.sub(_r4, line)

        if line != original and RE_DSN_ANY.search(line):
            # 改完还剩 DSN 字面量 → 说明没吃干净，回滚这行
            out_lines.append(original)
            skipped.append(original.strip()[:110])
        elif line != original:
            out_lines.append(line)
        else:
            out_lines.append(line)
            skipped.append(original.strip()[:110])

    return "".join(out_lines), changes, skipped


def process(path: str, rel: str, apply: bool) -> tuple[int, list[str], str]:
    src = open(path, encoding="utf-8-sig", errors="ignore").read()
    new, changes, skipped = transform(src)
    if changes == 0:
        return 0, skipped, "no-change"

    if not has_dsn_import(new):
        # 必须总是自举：这些文件自己的 sys.path 设置通常在本行之后才执行
        block = build_import_block(depth_of(rel))
        lines = new.splitlines(keepends=True)
        insert_at = 0
        for i, l in enumerate(lines[:40]):
            s = l.strip()
            if s.startswith("#!") or s.startswith("# -*-") or s.startswith("# coding"):
                insert_at = i + 1
        # 若模块开头是 docstring，插到它之后，避免把 __doc__ 弄丢
        try:
            tree = ast.parse(new)
            if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(
                    getattr(tree.body[0], "value", None), ast.Constant) and isinstance(
                    tree.body[0].value.value, str):
                insert_at = max(insert_at, tree.body[0].end_lineno or 0)
        except SyntaxError:
            pass
        # from __future__ 必须排在最前（ast.parse 不管这条，compile 才管）
        for i, l in enumerate(lines[:60]):
            if re.match(r"\s*from __future__ import", l):
                insert_at = max(insert_at, i + 1)
        new = "".join(lines[:insert_at]) + block + "".join(lines[insert_at:])

    # 用 compile 而不是 ast.parse：只有 compile 会校验 __future__ 的位置
    try:
        compile(new, path, "exec")
    except (SyntaxError, ValueError) as e:
        return 0, [f"编译校验失败，跳过 {rel}: {e}"], "compile-fail"

    if apply:
        open(path, "w", encoding="utf-8").write(new)
    return changes, skipped, "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写盘（默认只报告）")
    ap.add_argument("--only", default="", help="只处理指定文件（相对路径，逗号分隔）")
    ap.add_argument("--force", action="store_true", help="配合 --only：绕过排除名单与 ≥3 字面量保护（人工确认后再用）")
    args = ap.parse_args()

    targets: list[tuple[str, str]] = []
    if args.only:
        for rel in args.only.split(","):
            rel = rel.strip()
            if rel:
                targets.append((os.path.join(ROOT, rel), rel))
    else:
        for d in SCAN_DIRS:
            base = os.path.join(ROOT, d)
            for dp, dn, fn in os.walk(base):
                dn[:] = [x for x in dn if x not in SKIP_PARTS]
                for f in fn:
                    if not f.endswith(".py"):
                        continue
                    full = os.path.join(dp, f)
                    rel = os.path.relpath(full, ROOT)
                    if any(p in SKIP_PARTS for p in rel.split(os.sep)):
                        continue
                    targets.append((full, rel))

    total_files = total_changes = 0
    all_skipped: list[tuple[str, str]] = []
    for full, rel in targets:
        # ① 这些文件自己就定义了 DSN 策略，必须人工改，不能被批量替换
        if rel in EXCLUDE_REL and not (args.only and args.force):
            all_skipped.append((rel, "SKIP: 本文件是 DSN 策略/权威配置的定义处，需人工"))
            continue
        # ② 一个文件里有 ≥3 个 DSN 字面量，通常是多候选探活/兜底逻辑，语义敏感
        try:
            raw = open(full, encoding="utf-8-sig", errors="ignore").read()
        except OSError:
            continue
        if len(RE_DSN_ANY.findall(raw)) >= 3 and not (args.only and args.force):
            all_skipped.append((rel, f"SKIP: 含 {len(RE_DSN_ANY.findall(raw))} 个 DSN 字面量，疑似多候选兜底，需人工"))
            continue
        try:
            ch, skipped, status = process(full, rel, args.apply)
        except Exception as e:                      # noqa: BLE001
            all_skipped.append((rel, f"异常: {e}"))
            continue
        if ch:
            total_files += 1
            total_changes += ch
            print(f"  {'改' if args.apply else '将改'} {rel}: {ch} 处")
        for s in skipped:
            all_skipped.append((rel, s))

    print(f"\n{'已改' if args.apply else '将改'}: {total_changes} 处 / {total_files} 个文件")
    print(f"未处理（需人工判断）: {len(all_skipped)} 行")
    for rel, s in all_skipped[:25]:
        print(f"  {rel}\n      {s}")
    if len(all_skipped) > 25:
        print(f"  …另有 {len(all_skipped) - 25} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
