# -*- coding: utf-8 -*-
"""浪型状态的**唯一读取入口**（2026-10-21 步 3 ✓）。

背景：此前 8 个后端模块各读各的 `data/wave_state.json` ✗（`p2_entry_gate` / `wolf_253_build` /
`wolf_discipline` / `position_tier` / `wolf_judge` / `wave_gate` / `wolf_pick.*` / `daily_decision`），
回测取档还要读稀疏的 `wave_state_history/<sim8>.json` ✗。现已写入 PostgreSQL（表 `wave_state`，
**session_date 主键** ✓），统一从这里读 ✓。

口径（`WOLF_WAVE_STORE` ✓ 默认 `both`）：
  · `both`：先 PG ✓ → 读不到/异常再读文件 ✓（默认 ✓ 灰度期用这个 ✓）
  · `pg`  ：只用 PG ✗ 不回落（PG 里一旦确认齐全可切 ✓）
  · `file`：只用文件 ✓（回滚开关 ✓）

纪律：
  · **任何异常都返回 None**，由调用方 fail-open ✓（绝不因为读档失败拦死交易 ✗）；
  · 30 秒进程内缓存 ✓（踩过"缓存无 TTL 冻住"的坑 ✗ —— 有 TTL 就行 ✓）；
  · PG 查询走 `connect_timeout=5` ✓，不让慢库拖住交易路径 ✗。
"""
import json
import os
import time
from typing import Any, Dict, Optional

_MODE = (os.environ.get("WOLF_WAVE_STORE") or "both").strip().lower()     # both | pg | file
_TTL = 30.0
_CACHE: Dict[str, Any] = {"at": 0.0, "latest": None, "asof": {}}


def _data_dir() -> str:
    """与全仓同口径：DATA_DIR 为空串时退回 `data` ✓（踩过空串变 ./ 的坑 ✗）。"""
    d = (os.environ.get("DATA_DIR") or "").strip()
    if d:
        return d
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(root, "data")


def _file_state(name: str = "wave_state.json") -> Optional[Dict[str, Any]]:
    try:
        with open(os.path.join(_data_dir(), name), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _pg_latest() -> Optional[Dict[str, Any]]:
    try:
        import sys
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        p = os.path.join(root, "apps", "main_line")
        if p not in sys.path:
            sys.path.insert(0, p)
        import wave_pg          # type: ignore
        return wave_pg.load_latest()
    except Exception:
        return None


def _pg_asof(sim8: Any) -> Optional[Dict[str, Any]]:
    try:
        import sys
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        p = os.path.join(root, "apps", "main_line")
        if p not in sys.path:
            sys.path.insert(0, p)
        import wave_pg          # type: ignore
        return wave_pg.load_asof(sim8)
    except Exception:
        return None


def get_wave() -> Optional[Dict[str, Any]]:
    """当前档（PG 优先 ✓ 文件兜底 ✓）。读不到 ⇒ None（调用方须 fail-open ✓）。"""
    now = time.time()
    if _CACHE.get("latest") is not None and (now - float(_CACHE.get("at") or 0)) < _TTL:
        return _CACHE["latest"]
    w = None
    if _MODE in ("both", "pg"):
        w = _pg_latest()
    if not w and _MODE in ("both", "file"):
        w = _file_state()
    _CACHE["latest"] = w
    _CACHE["at"] = now
    return w


def get_wave_asof(sim8: Any) -> Optional[Dict[str, Any]]:
    """回测取档：`session_date <= sim8` 的最近一档 ✓（**替代** wave_state_history/<sim8>.json ✓）。"""
    key = str(sim8 or "").replace("-", "")[:8]
    if not key:
        return get_wave()
    now = time.time()
    hit = _CACHE["asof"].get(key)
    if hit and (now - hit[0]) < _TTL:
        return hit[1]
    w = None
    if _MODE in ("both", "pg"):
        w = _pg_asof(key)
    if not w and _MODE in ("both", "file"):
        w = _file_state("wave_state_history/%s.json" % key) or _file_state(
            "wave_state_%s-%s-%s.json" % (key[:4], key[4:6], key[6:8]))
    _CACHE["asof"][key] = (now, w)
    return w


def clear_cache() -> None:
    _CACHE["latest"] = None
    _CACHE["at"] = 0.0
    _CACHE["asof"] = {}


def mode() -> str:
    return _MODE
