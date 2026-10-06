# -*- coding: utf-8 -*-
"""scan_undefined_names.py — 静态找「用了但作用域里没有这个名字」的潜在 NameError。

为什么需要（账本 §9.699 ✓）：
    用户 QQ 收到 `[ALERT] tmonitor.py:4565 | : raise×1：name 'p' is not defined` ✗。
    本机三个来源都排除了（仓库 6548 行无此 bug、钉住副本 3574 行、178 个 alerts.jsonl
    里没有任何 tmonitor 记录）⇒ **那条报警来自云端部署** ✓。
    ⇒ 于是把「按作用域查未定义名」做成可复用脚本 ✓，云端也能一键自查 ✓。

用法：
    python jobs/scan_undefined_names.py [文件或目录 ...]     # 默认扫 backend/app/services
    python jobs/scan_undefined_names.py --all                # 扫全仓 py（跳过 data/）
退出码：发现可疑 ⇒ 1；干净 ⇒ 0（便于挂 CI/预检 ✓）。

注意（**不是**万能）✓：
  · 只查「名字在**所有**外层作用域（含模块级、内建）都不存在」的情况 ⇒ 即真正的 NameError ✓
  · **查不出** UnboundLocalError（"局部变量在赋值前被引用" ✗）—— 那类要用运行时 traceback ✓
"""
from __future__ import annotations

import ast
import builtins
import os
import sys
from typing import Dict, List, Set, Tuple

BUILTINS = set(dir(builtins))
SKIP_DIRS = {'data', '.git', 'node_modules', '__pycache__', '.venv', '.dsh-tmp', 'frontend'}


def _bound(node: ast.AST) -> Set[str]:
    """一个作用域内**绑定**的全部名字（不含嵌套函数体 ✓）。"""
    out: Set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n is not node:
            out.add(n.name)
            continue
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                out.add((al.asname or al.name).split('.')[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, ast.Global):
            out.update(n.names)
    return out


def scan_file(path: str) -> List[Tuple[int, str, str]]:
    try:
        src = open(path, encoding='utf-8').read()
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return []
    mod = _bound(tree)
    mod |= BUILTINS
    # ★ 有 from __future__ import annotations ⇒ 注解不求值 ⇒ 注解里的名字不算未定义 ✓
    _fut = any(isinstance(n, ast.ImportFrom) and n.module == '__future__'
               and any(a.name == 'annotations' for a in n.names) for n in tree.body)
    # 模块自带 dunder（__file__/__name__/__doc__/__package__ 等 ✓）+ 常见注解名 ✓
    mod |= {'__file__', '__name__', '__doc__', '__package__', '__spec__', '__loader__',
            '__builtins__', '__debug__', 'annotations'}
    bad: List[Tuple[int, str, str]] = []
    ann_lines: Set[int] = set()
    if _fut:
        for n in ast.walk(tree):
            a = getattr(n, 'annotation', None)
            if a is not None:
                for x in ast.walk(a):
                    if hasattr(x, 'lineno'):
                        ann_lines.add(x.lineno)
            if getattr(n, 'returns', None) is not None:
                for x in ast.walk(n.returns):
                    if hasattr(x, 'lineno'):
                        ann_lines.add(x.lineno)
    lines = src.split('\n')

    def walk(node: ast.AST, stack: List[Set[str]], names: List[str]) -> None:
        for c in ast.iter_child_nodes(node):
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(c, stack + [_bound(c)], names + [getattr(c, 'name', '?')])
            elif isinstance(c, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                walk(c, stack + [_bound(c)], names + ['<lambda/comp>'])
            elif isinstance(c, ast.Name) and isinstance(c.ctx, ast.Load):
                if c.lineno in ann_lines:
                    pass   # 注解上下文 ⇒ 不求值 ⇒ 跳过 ✓
                elif not any(c.id in s for s in stack):
                    bad.append((c.lineno, ' > '.join(names) or '<module>',
                                ('未定义名=%s | ' % c.id) + lines[c.lineno - 1].strip()[:96]))
            else:
                walk(c, stack, names)

    walk(tree, [mod], [])
    return bad


def iter_py(targets: List[str]) -> List[str]:
    out: List[str] = []
    for t in targets:
        if os.path.isfile(t) and t.endswith('.py'):
            out.append(t)
        elif os.path.isdir(t):
            for root, dirs, files in os.walk(t):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                out.extend(os.path.join(root, f) for f in files if f.endswith('.py'))
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--all' in sys.argv or not args:
        targets = ['.'] if '--all' in sys.argv else [os.path.join('backend', 'app', 'services')]
    else:
        targets = args
    files = iter_py(targets)
    total = 0
    for f in files:
        for ln, where, code in scan_file(f):
            total += 1
            if total <= 40:
                print('  %s:%d | %s | %s' % (f, ln, where, code))
    print('  ⇒ 扫描 %d 个文件，可疑未定义名 **%d 处** %s' % (len(files), total, '✓' if not total else '✗'))
    return 1 if total else 0


if __name__ == '__main__':
    raise SystemExit(main())
