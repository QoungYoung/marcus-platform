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
import time
from typing import Any, Dict, Optional

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


_STATE_CACHE: Dict[str, Any] = {"key": None, "ts": 0.0, "val": None}
_STATE_TTL = 60.0     # 秒（长驻进程里必须能失效 ✓ 2026-10-20 教训）


def ladder_state(as_of: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """用**浪型链回放**算出 as_of 当日的 wave_state（**带单向锁** ✓，与 live/卡片同源 ✓）。

    用途：`wave_gate.ensure_wave` 的「现场补」—— 原来那条走 `wave_level.judge_wave`（**旧 LLM 判据** ✗），
    浪型链接管后必须换成本函数 ✓（2026-10-20 用户要求修复 ✓）。

    纪律：
      · **只读不写盘** ✓（live 的写权归 08:10 的 `wave_ladder_state` ✓，后端不抢写 ✗）；
      · 任何异常 ⇒ None（fail-open ✓，上层放行）；
      · 60 秒进程内缓存 ✓（walk 一次约数秒，不缓存会被每次请求拖慢 ✗）。
    """
    m = _load()
    if not m:
        return None
    try:
        key = str(as_of or "")[:8]
        now = time.time()
        if _STATE_CACHE["key"] == key and (now - float(_STATE_CACHE["ts"] or 0)) < _STATE_TTL:
            return _STATE_CACHE["val"]
        rows = m.load_rows()
        if not rows:
            return None
        if key:
            rows = [r for r in rows if r[0] <= key] or rows
        evs = m.walk(rows)
        if not evs:
            return None
        ws = m.to_wave_state(evs[-1])
        _STATE_CACHE.update({"key": key, "ts": now, "val": ws})
        return ws
    except Exception:
        return None


def adopt_wave_op(op: Optional[str], asof: Optional[str] = None) -> Optional[str]:
    """浪型链的接管入口；**任何异常 ⇒ 原样返回**（生产安全优先 ✓）。"""
    m = _load()
    if not m:
        return op
    try:
        return m.adopt_operation(op, asof)
    except Exception:
        return op
