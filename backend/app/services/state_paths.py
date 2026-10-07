# -*- coding: utf-8 -*-
"""**跨日状态的目录口径** ✓（账本 §9.248）—— 生产与回测各得其所 ✓。

问题（审计 §9.246-E，41 处 ✗）：不少**状态文件**（无日期后缀 ✓，如 `roundtrip_state.json` ✓、
  `wolf_hedge_refill.json` ✓、`wolf_discipline.json` ✓）直接写在 `DATA_DIR` 下 ✓。
  生产里 `DATA_DIR` 是**单一目录** ✓ ⇒ 没问题 ✓；
  但**回测里 `DATA_DIR` 是"按日目录"** ✗（`<run>/20260105` ✓）⇒ **状态每天从零开始** ✗✗
  （实测：转正记录每天被覆盖 ✓；`roundtrip`／`hedge_refill` 这类"**跨日**才成立"的状态同理 ✗）

修法（**不破坏生产** ✓）：状态目录取
  ① `WOLF_STATE_ROOT`（**回测**在 pins 里设成**运行根** ✓）
  ② 否则 `DATA_DIR`（**生产**原样 ✓，零改动 ✓）
用法：`from app.services.state_paths import state_dir` ⇒ `os.path.join(state_dir(), "x.json")` ✓
"""
from __future__ import annotations

import os


def state_dir() -> str:
    """**跨日状态**应落的目录 ✓（回测=运行根 ✓；生产=DATA_DIR ✓）。"""
    return (os.environ.get("WOLF_STATE_ROOT")
            or os.environ.get("DATA_DIR")
            or "/app/data")


def state_path(name: str) -> str:
    """`state_dir()` 下的一个文件路径 ✓。"""
    return os.path.join(state_dir(), name)
