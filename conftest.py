# -*- coding: utf-8 -*-
"""仓库根 conftest —— 统一把测试需要的目录放进 `sys.path`（账本 §9.717）。

## 为什么要有它 ✗

本仓库此前**没有 conftest.py** ✗ ⇒ 每个测试文件各自拼 `sys.path` ✓
（`REPO_ROOT = Path(__file__).resolve().parents[2]` ＋ 逐目录 insert ✓）。
逐文件写法的问题 ✓：**漏一个目录就整文件崩** ✗ —— 实测 ✗：
  · `ModuleNotFoundError: No module named 'core.qq_notifier'` ×35 ✗
  · `ModuleNotFoundError: No module named 'core.utils'` ×5 ✗
（`core/qq_notifier.py`、`core/utils.py` **确实存在** ✓，只是没在 `sys.path` 里 ✓）

## 口径

**只加路径、不改任何行为** ✓（幂等 ✓、不 import 任何业务模块 ✓ ⇒ 无副作用 ✓）。
目录与既有测试逐字一致 ✓：`backend` / `core` / `apps/paper-trading` / `apps/main_line` / 仓库根 ✓。
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
for _d in (
    _REPO_ROOT / "backend",
    _REPO_ROOT / "core",
    _REPO_ROOT / "apps" / "paper-trading",
    _REPO_ROOT / "apps" / "main_line",
    _REPO_ROOT,
):
    _s = str(_d)
    if _s not in sys.path:
        sys.path.insert(0, _s)


# ── ★ 账本 §9.717 ✓：**测试间隔离** ──────────────────────────────────────────
#   实测 ✗：两个测试文件**一起跑**会比各自单独跑**多出 5 个失败**（顺序依赖 ✓）——
#     典型原因：某个测试改了 `os.environ`（或全局开关 ✓）而**没有还原** ✗
#     ⇒ 后续测试读到被污染的环境 ✓（例如 DATABASE_URL / WOLF_* 开关 ✓）
#   ⇒ 这里用 pytest 的 autouse fixture：**每个测试结束后还原 `os.environ`** ✓
#   ★ 只碰环境变量、不碰业务状态 ✓；对本来就写对（自己还原）的测试**零影响** ✓
import os as _os

import pytest as _pytest


@_pytest.fixture(autouse=True)
def _restore_environ_after_test():
    """测试结束还原 `os.environ`（防跨测试污染 ✓）。"""
    _snapshot = dict(_os.environ)
    yield
    for _k in list(_os.environ.keys()):
        if _k not in _snapshot:
            _os.environ.pop(_k, None)
    for _k, _v in _snapshot.items():
        if _os.environ.get(_k) != _v:
            _os.environ[_k] = _v
