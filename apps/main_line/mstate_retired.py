# -*- coding: utf-8 -*-
"""已退役 state 字段的统一出口（§9.830）

背景（用户 2026-10-13「现在都迁移完了吧」✓）：2026-09-13 的迁移把 `main_line_state.json` 的
  `catalyst` / `catalyst_source` / `fusion` / `mainline_gate` **有意移除** ✗
  （见 `mainline_state_inject.py:134-139` ✓；生产 state 现只剩 date/updated_at/candidates/
    main_line/main_line_source/mainline_select_rows/mainline_select ✓）
  ⇒ 但全仓还有 ~12 处仍在 `.get("catalyst")` / `.get("fusion")` / `.get("mainline_gate")` ✗
    ⇒ 它们**已经**在静默降级（永远拿到 {} ✗），只是读者看不出来 ✗

本模块把这件事**显式化** ✓：
  · `get_retired(state, key)` —— 返回空（与现状**逐位一致** ✓，不改行为 ✗），但**一次性告警** ✓
  · 想彻底清理时，按告警清单把这些分支删掉 ✓（方向层口径见 `switch_builder.theme_top12` / `mainline_select.r5`）
"""
from __future__ import annotations

import os
from typing import Any, Dict

RETIRED: Dict[str, str] = {
    "catalyst": "2026-09-13 起退役（研报线关闭；方向层口径见 mainline_select / r5）",
    "catalyst_source": "同上 ✓",
    "fusion": "2026-09-13 起退役（改由 mainline_select 承担 ✓）",
    "mainline_gate": "2026-09-13 起退役（WOLF_INJECT_GATE=1 时才重新注入 ✓）",
}
_WARNED: set = set()


def get_retired(state: Any, key: str, default: Any = None, where: str = "") -> Any:
    """读一个**已退役**字段：恒返回 default ✓（与现状一致 ✗），但首次访问告警一次 ✓"""
    if key in RETIRED and key not in _WARNED:
        _WARNED.add(key)
        try:
            print("[mstate_retired] ⚠️ 读取已退役字段 %s（%s）@ %s ⇒ 恒为空 ✓（行为与现状一致 ✓；"
                  "建议清理该分支 ✓）" % (key, RETIRED[key], where or "?"), flush=True)
            from app.services import alert_hub as _ah
            if str(os.getenv("WOLF_RETIRED_FIELD_QQ", "0")).strip().lower() in ("1", "true", "yes"):
                _ah.push_qq("🟠 仍在读已退役 state 字段 `%s`（%s）⇒ 恒为空 ✓ 建议清理 ✓"
                            % (key, where or "?"))
        except Exception:
            pass
    return default
