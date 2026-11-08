# -*- coding: utf-8 -*-
"""core/wave.py — 浪型当前档的轻量读入口（唯一真源是 PG 表 `wave_state`）

为什么需要这一层：
    作业/脚本进程里直接 `from app.services.wave_store import get_wave` 需要 `backend/`
    在 sys.path 上，而这个前置条件每个脚本都要自己拼一遍；拼错就退回读文件。
    这里把它收成一处：`from core.wave import current`。

为什么不让调用方自己去读文件：
    2026-11-04 起已经「停写文件、PG 唯一真源」，`data/wave_state.json` 是 11-04 冻结的
    导出物。仍直接读它的代码拿到的是**过期档**（2026-11-08 审计发现 5 个读点还在读文件）。

纪律（与全仓一致）：
    · 任何异常 ⇒ 返回 `{}`，由调用方 fail-open（绝不因为浪型读不到就拦死交易）；
    · 不缓存（wave_store 内部已有 30s TTL，这里再加一层只会让过期更久）。
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_path() -> None:
    for p in (os.path.join(_ROOT, "backend"), _ROOT):
        if p and p not in sys.path:
            sys.path.insert(0, p)


def current() -> dict:
    """当前档（PG）。读不到返回 `{}`。"""
    _ensure_path()
    try:
        from app.services.wave_store import get_wave
        return get_wave() or {}
    except Exception:
        return {}


def asof(sim8) -> dict:
    """回测取档：`session_date <= sim8` 的最近一档。读不到返回 `{}`。"""
    _ensure_path()
    try:
        from app.services.wave_store import get_wave_asof
        return get_wave_asof(sim8) or {}
    except Exception:
        return {}


def operation() -> str:
    """当前操作口径（build / t_only / side / defense / exit），读不到返回 `side`。"""
    return str(current().get("operation") or "side").strip().lower()


__all__ = ["current", "asof", "operation"]
