# -*- coding: utf-8 -*-
"""wave_ladder_gate.py — backend 侧薄适配层（**唯一**做 sys.path 处理的地方）。

先例：backend/app/services/wolf_theme_resilience.py:125 同样用候选目录 + sys.path 引 apps/main_line。

用法（消费点各改一行，其它逻辑不动）：

    from app.services.wave_ladder_gate import adopt_wave_op
    op = adopt_wave_op(op)        # WOLF_WAVE_LADDER=0（默认）⇒ 原样返回 ⇒ 生产逐字不变 ✓

开关（在 apps/main_line/wave_ladder.py 里读）：
  · WOLF_WAVE_LADDER（默认 0）＝影子（只留痕 wave_ladder_ab.jsonl）/ 1＝由浪型链**接管** operation；
  · 算不出（缺指数数据等）⇒ 回落生产 op（fail-open ✓）；适配层任何异常也一律原样返回 ✓。
"""
from __future__ import annotations

import os
import sys
from typing import Any, Optional

_MOD: Any = None


def _load() -> Any:
    """懒加载 apps/main_line/wave_ladder；失败缓存为 False（不反复 IO）✓。"""
    global _MOD
    if _MOD is not None:
        return _MOD
    try:
        import wave_ladder as _wl          # 已经被别处加进 sys.path 的情形
        _MOD = _wl
        return _MOD
    except ImportError:
        pass
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    for cand in ("/app/apps/main_line", os.path.join(here, "apps", "main_line")):
        if cand and os.path.isdir(cand) and cand not in sys.path:
            sys.path.insert(0, cand)
    try:
        import wave_ladder as _wl
        _MOD = _wl
    except Exception:
        _MOD = False
    return _MOD


def adopt_wave_op(op: Optional[str], asof: Optional[str] = None) -> Optional[str]:
    """浪型链的接管入口；**任何异常 ⇒ 原样返回**（生产安全优先 ✓）。"""
    m = _load()
    if not m:
        return op
    try:
        return m.adopt_operation(op, asof)
    except Exception:
        return op
