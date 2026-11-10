# -*- coding: utf-8 -*-
"""state_io.py — 给历史代码的 `load(name)` / `_load(path)` 用的兼容层

用法（在既有的 load 助手最前面加两行，调用点不用改）：

    def load(p):
        _pg = state_io.maybe_load(p)      # ← 对 main_line_state / position_class / wave_state 生效
        if _pg is not None:
            return _pg
        ...原有逻辑...

为什么需要它：
    这些状态文件有 20+ 个读取点，分散在 20+ 个文件里，每个文件都有自己的 load 助手、
    签名还不一样（`load(p)` / `_load(name)` / `_load(name, default)`）。
    逐个改调用点等于改 29 处表达式；改助手只改 13 处，而且**调用点零风险**。

口径：
    只认这三个文件名（按 basename 匹配，传路径也行），其它文件一律返回 None，
    让调用方按原逻辑读——保证不会改变任何无关行为。
    wave_state 走 core.wave.current（PG 唯一真源）；2026-11-08 起文件已停写并删除。
"""
from __future__ import annotations

import os

NAMES = {
    "main_line_state.json",
    "position_class_result.json",
    "wave_state.json",
}


def _loaders():
    """惰性导入，避免在 import 期就把 DB 依赖拉进来。"""
    try:
        from state_pg import load_main_line_state, load_position_class  # 同目录脚本式导入
    except Exception:
        try:
            from .state_pg import load_main_line_state, load_position_class  # 包式导入
        except Exception:
            return None, None
    return load_main_line_state, load_position_class


def _wave_pg() -> dict | None:
    """浪型 → core.wave.current（PG 唯一真源）；读不到返回 None（调用方继续原逻辑）。"""
    try:
        import sys
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if root not in sys.path:
            sys.path.insert(0, root)
        from core.wave import current
        w = current() or {}
        return w if isinstance(w, dict) and w else None
    except Exception:
        return None


def maybe_load(p) -> dict | None:
    """目标属于三个状态文件之一 → 返回 PG 的内容；否则 None（调用方继续原逻辑）。"""
    name = os.path.basename(str(p or "").replace("\\", "/"))
    if name not in NAMES:
        return None
    if name == "wave_state.json":
        return _wave_pg()
    ml, pc = _loaders()
    try:
        if name == "main_line_state.json":
            return ml() if ml else None
        return pc() if pc else None
    except Exception:
        return None
