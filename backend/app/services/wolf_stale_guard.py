# -*- coding: utf-8 -*-
"""陈旧环境产物护栏（内容级 as-of 校验）。

背景（2026-09-19 排查，与 `wolf_context._latest()` 的文件名级过滤互补）：
  有一类"固定文件名 + 内容自带 as_of"的环境产物，回放沙箱会带着 **repo 里的 9 月桩**：
    · `wolf_volume_gate.json`  → `as_of: 20260915` + 9/02–9/15 的两市成交额序列（tag=地量）
    · 同类还有主题浪 / mainline_gate / A9 等由 agent 或日更脚本产出的固定名 JSON。
  文件名里没有日期 ⇒ `WOLF_ASOF_FILE_FILTER`（按文件名日期过滤）**拦不住**，
  于是 2026 年 1 月的回放会拿 9 月的量能/结构去判当天，且**静默**。
做法：读 JSON 后校验其内部日期字段（as_of / date / updated_at / trade_date / ts），
  距"钉住当日"（回放里 `datetime.now()` 已被 bt_run_pinned 偏移到 as-of 当天）超过 N 天 ⇒ 视为**缺失**：
  返回 `{}` 并打印一次留痕（fail-open：宁可当没有数据，也不要拿过期数据做判据）。
开关：`WOLF_STALE_ARTIFACT_GUARD`（库内默认关 = 生产逐位不变；回测默认开）、
      `WOLF_STALE_MAX_DAYS`（默认 10 天；跨长假留余量）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass


GUARD_ON = str(os.getenv("WOLF_STALE_ARTIFACT_GUARD",
                         "1" if os.getenv("BT_ASOF_FETCH") else "0")).strip().lower() in ("1", "true", "yes", "on")
try:
    MAX_DAYS = float(os.getenv("WOLF_STALE_MAX_DAYS", "10"))
except Exception:
    MAX_DAYS = 10.0

_STATS: Dict[str, int] = {"checked": 0, "stale": 0, "no_date": 0, "errors": 0}
_SEEN: set = set()
_DATE_KEYS = ("as_of", "date", "trade_date", "updated_at", "generated_at", "ts", "timestamp")


def stats() -> Dict[str, Any]:
    return {"on": GUARD_ON, "max_days": MAX_DAYS, **_STATS}


def _parse(v: Any) -> Optional[datetime]:
    """YYYYMMDD / YYYY-MM-DD / ISO / epoch 秒 → datetime；解析不了返回 None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().isdigit() and len(v.strip()) >= 10):
        try:
            return datetime.fromtimestamp(float(v))
        except Exception:
            return None
    s = str(v).strip().replace("/", "-")
    s8 = s.replace("-", "")[:8]
    for fmt, txt in (("%Y%m%d", s8), ("%Y-%m-%d", s.replace("-", "")[:8] and "%s-%s-%s" % (s8[:4], s8[4:6], s8[6:8]))):
        try:
            return datetime.strptime(txt, fmt)
        except Exception as _e_sil1:
            _silent_alert("wolf_stale_guard.py:54", _e_sil1)
            continue
    try:
        return datetime.fromisoformat(str(v).strip()[:19])
    except Exception:
        return None


def as_of_of(obj: Dict[str, Any]) -> Optional[datetime]:
    for k in _DATE_KEYS:
        if isinstance(obj, dict) and obj.get(k):
            d = _parse(obj.get(k))
            if d:
                return d
    return None


def is_stale(obj: Dict[str, Any], max_days: Optional[float] = None) -> Tuple[bool, str]:
    """(是否陈旧, 说明)。无日期字段 ⇒ False（无法判定不拦，只计数）。"""
    if not GUARD_ON:
        return False, "护栏关(WOLF_STALE_ARTIFACT_GUARD=0)"
    d = as_of_of(obj or {})
    if d is None:
        _STATS["no_date"] += 1
        return False, "无日期字段 → 不判陈旧"
    days = (datetime.now() - d).total_seconds() / 86400.0
    lim = MAX_DAYS if max_days is None else float(max_days)
    # ⚠️ 两个方向都要判：
    #   · days > lim  → 过期（沙箱带着 repo 很久以前的产物）
    #   · days < -1   → **未来日期**（回放里最常见的形态：钉住日是 2026-01，而产物 as_of 是 2026-09）
    #     只判"过期"会漏掉未来桩 —— 2026-09-19 的进程读 20260915 只差 4.4 天，看着"很新"。
    if days > lim:
        return True, "as_of=%s 早于当日 %.1f 天 > %.0f 天（过期产物）" % (d.strftime("%Y%m%d"), days, lim)
    if days < -1:
        return True, "as_of=%s 晚于当日 %.1f 天（未来产物，as-of 不可用）" % (d.strftime("%Y%m%d"), -days)
    return False, "as_of=%s（%.1f 天）" % (d.strftime("%Y%m%d"), days)


def check(obj: Dict[str, Any], tag: str = "", max_days: Optional[float] = None) -> Tuple[bool, str]:
    """便捷入口：返回 (ok_to_use, why)；陈旧 ⇒ (False, why) 并留痕（同 tag 只打一次）。"""
    st, why = is_stale(obj or {}, max_days)
    if st:
        _STATS["stale"] += 1
        key = tag or "?"
        if key not in _SEEN:
            _SEEN.add(key)
            print("[stale-guard] %s 内容过期 → 视为缺失（fail-open）：%s" % (key, why), flush=True)
        return False, why
    return True, why


def load_json(path: str, tag: str = "", max_days: Optional[float] = None) -> Dict[str, Any]:
    """读 JSON 并做内容级新鲜度校验；陈旧/读失败 ⇒ 返回 {}（调用方按"无数据"处理）。"""
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f) or {}
    except Exception:
        _STATS["errors"] += 1
        return {}
    _STATS["checked"] += 1
    ok, _why = check(obj, tag or os.path.basename(path), max_days)
    return obj if ok else {}
