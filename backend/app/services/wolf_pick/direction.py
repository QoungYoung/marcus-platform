# -*- coding: utf-8 -*-
"""方向层读取（P0-4）。

输入 `main_line_state.json`（生产由 mainline_select 写），输出 Top1-3 方向。
本模块**不读 gate**：A/B 报告（docs/wolf-p0-ab-report.md）显示池单独 top1 54.2%/top3 84.7%，
池∩gate 51.4%/77.8%；gate 默认不参与选择。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _dedupe(items) -> List[str]:
    out, seen = [], set()
    for x in items:
        s = str(x or "").strip()
        if s and s not in seen and s not in ("None", "非主题"):
            seen.add(s)
            out.append(s)
    return out


def select_directions(state: Optional[Dict[str, Any]], topk: int = 3) -> List[Dict[str, Any]]:
    """从 main_line_state 选方向；兼容新旧两种结构。"""
    if not isinstance(state, dict) or not state:
        return []
    sel = state.get("mainline_select") if isinstance(state.get("mainline_select"), dict) else {}
    r5 = sel.get("r5") if isinstance(sel.get("r5"), dict) else {}
    pool = [str(x) for x in (sel.get("pool") or [])]
    themes = [str((t or {}).get("theme") or "") for t in (sel.get("themes") or [])]
    # `pool_top` 只作上下文，不进选择（可能是池外主题，r5=0 会把 Top3 撑虚）
    names = _dedupe([sel.get("mainline"), sel.get("second")] + pool + themes
                    + list(state.get("candidates") or []) + [state.get("main_line")])
    if not names:
        return []
    rows = []
    for i, theme in enumerate(names):
        try:
            score = float(r5.get(theme))
        except (TypeError, ValueError):
            score = 0.0
        rows.append({
            "theme": theme,
            "rank": i + 1,
            "score": round(score, 6),
            "in_pool": theme in pool,
            "basis": {"r5": round(score, 6), "in_pool": theme in pool,
                      "source": "mainline_select" if sel else "main_line_state"},
        })
    # 有 r5 时按分数稳定排序；全 0 时保留状态里的原顺序
    if any(r["score"] for r in rows):
        rows.sort(key=lambda r: (-r["score"], r["rank"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows[:max(1, int(topk))]
