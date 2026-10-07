# -*- coding: utf-8 -*-
"""触发静默（当日不再为该 (标的, 腿) 生成重复触发）。

背景（2026-09-19 实测 data/_bt_jan7，18 天 2383 条触发）：
  · custom_vwap_sell 1232 + custom_support_sell 655 + high_sell 335 = 2222 条（93%）是 auto_exit 的
    **持续卖腿**在 4 只持仓票上全天反复触发；其中 blocked 主因是
    「自动执行量推导为 0（仅底仓无 T 仓可卖）」1213 条 + 「[G8] 上影线≥30%」752 条。
  · 这些重复触发不花 LLM（真正 agent 认领仅 114 条 = 4.8%），但每条都要算 quote / 写库，
    拖慢回放（0128 因当日 41 个标的逐分钟跑而明显变慢）并污染统计。
口径（**只减少重复生成，不新增交易判据**）：
  · 某 (标的, 腿) 一旦被判「量推导为 0（无 T 仓可卖）」或命中 G8 上影线停机，**当日该腿静默**；
  · 狼大原话「看见出上影线 立马停止做T」本身就是当日停做 T，而不是每 5 分钟再问一次。
开关：`WOLF_TRIGGER_MUTE`（库内默认关、回测开）；状态文件 `$DATA_DIR/trigger_mute_<当日>.json`。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

ON = str(os.getenv("WOLF_TRIGGER_MUTE",
                   "1" if os.getenv("BT_ASOF_FETCH") else "0")).strip().lower() in ("1", "true", "yes", "on")
_STATS: Dict[str, int] = {"muted": 0, "skipped": 0, "logged": 0, "err": 0}
_CACHE: Dict[str, Any] = {"path": None, "mtime": None, "data": {}}
_LOGGED: set = set()


def enabled() -> bool:
    return ON


def stats() -> Dict[str, Any]:
    return {"on": ON, **_STATS}


def _day8(day: str = "") -> str:
    d = str(day or "").replace("-", "")
    return d or datetime.now().strftime("%Y%m%d")


def _path(day: str = "") -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/app/data"), "trigger_mute_%s.json" % _day8(day))


def _key(symbol: str, kind: str) -> str:
    return "%s|%s" % (str(symbol).upper(), str(kind or ""))


def _load(day: str = "") -> Dict[str, Any]:
    p = _path(day)
    try:
        mt = os.path.getmtime(p)
    except OSError:
        _CACHE.update({"path": p, "mtime": None, "data": {}})
        return {}
    if _CACHE.get("path") == p and _CACHE.get("mtime") == mt:
        return _CACHE.get("data") or {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        _STATS["err"] += 1
        data = {}
    _CACHE.update({"path": p, "mtime": mt, "data": data})
    return data


def is_muted(symbol: str, kind: str, day: str = "") -> bool:
    """该 (标的, 腿) 当日是否已静默；首次命中时打印一行（便于审计）。"""
    if not ON:
        return False
    hit = bool((_load(day) or {}).get(_key(symbol, kind)))
    if hit:
        _STATS["skipped"] += 1
        k = _key(symbol, kind)
        if k not in _LOGGED:
            _LOGGED.add(k)
            _STATS["logged"] += 1
            print("[TMonitor] 触发静默：%s %s 当日已判定不再生成（见 trigger_mute 文件）" % (symbol, kind), flush=True)
    return hit


def mute(symbol: str, kind: str, why: str, day: str = "") -> None:
    """把 (标的, 腿) 记为当日静默。任何异常都不抛（fail-open，不影响主流程）。"""
    if not ON:
        return
    try:
        p = _path(day)
        data = dict(_load(day) or {})
        k = _key(symbol, kind)
        if k in data:
            return
        data[k] = {"why": str(why)[:120], "at": datetime.now().strftime("%H:%M:%S")}
        d = os.path.dirname(p)
        if not os.path.isdir(d):
            return
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".tm_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        _STATS["muted"] += 1
        _CACHE.update({"path": p, "mtime": None, "data": None})     # 让下次读刷新
        print("[TMonitor] 触发静默登记 %s %s：%s" % (symbol, kind, str(why)[:60]), flush=True)
    except Exception:
        _STATS["err"] += 1
