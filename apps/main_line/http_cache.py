# -*- coding: utf-8 -*-
"""直连取数的本地缓存 + 硬超时 + 负缓存（2026-09-19 用户拍板）。

背景（实测）：0128 的 08:18 布腿器耗时 **958 秒**，日志里是
  `[tushare_relay] moneyflow 走 datahubco 失败，尝试下一源: HTTP 502`
根因：部分取数走的是**裸 requests 直连**（如 `position_class.live_hedge_act()` 打 pcd.mobcvb.cn
的 `moneyflow_ind_dc`，timeout=30 且往前最多试 5 天），**绕过了 bt_asof_fetch 的磁盘缓存**
(`.dsh-tmp/wolfbt/asof_fetch_cache/`) ⇒ 每次跑批都重打网络，遇 502 就要几十秒~几分钟。
而分钟档 / 日线早就是共享落地缓存（`data/_bt_full/mins/` 5035 文件、0128 新写 0）。

本模块提供 get_json()：按 (url, params, asof) 落盘缓存，成功写正缓存、失败写**负缓存**（短 TTL），
并且**硬超时**默认 6 秒（覆盖调用方传的 30/40 秒）——避免单次网络抖动把一天拖成十几分钟。
开关：`WOLF_HTTP_CACHE`（库内默认关、回测开）、`WOLF_HTTP_TIMEOUT`（默认 6 秒）、
      `WOLF_HTTP_NEG_TTL`（负缓存秒数，默认 900）、`WOLF_HTTP_CACHE_DIR`（默认 .dsh-tmp/wolfbt/http_cache）。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, Optional


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


ON = str(os.getenv("WOLF_HTTP_CACHE", "1" if os.getenv("BT_ASOF_FETCH") else "0")).strip().lower() in ("1", "true", "yes", "on")
try:
    TIMEOUT = float(os.getenv("WOLF_HTTP_TIMEOUT", "6"))
except Exception:
    TIMEOUT = 6.0
try:
    NEG_TTL = float(os.getenv("WOLF_HTTP_NEG_TTL", "900"))
except Exception:
    NEG_TTL = 900.0
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE = os.getenv("WOLF_HTTP_CACHE_DIR") or os.path.join(_REPO, ".dsh-tmp", "wolfbt", "http_cache")
_STATS: Dict[str, int] = {"get": 0, "hit": 0, "neg_hit": 0, "miss": 0, "err": 0, "saved_timeout": 0}
_LOGGED: set = set()


def enabled() -> bool:
    return ON


def stats() -> Dict[str, Any]:
    return {"on": ON, "timeout": TIMEOUT, "neg_ttl": NEG_TTL, "dir": CACHE, **_STATS}


def _key(url: str, params: Optional[dict], asof: str) -> str:
    raw = "%s|%s|%s" % (url, json.dumps(params or {}, sort_keys=True, ensure_ascii=False), asof or "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _path(k: str) -> str:
    return os.path.join(CACHE, "%s.json" % k)


def get_json(url: str, params: Optional[dict] = None, headers: Optional[dict] = None,
             asof: str = "", verify: bool = False, timeout: Optional[float] = None,
             allow_negative: bool = True) -> Optional[Any]:
    """取 JSON：命中正缓存直接返回；命中未过期的负缓存直接返回 None（**不打网络**）；
    否则按**硬超时**外呼一次，成功写正缓存、失败写负缓存。任何异常都返回 None（fail-open）。"""
    _STATS["get"] += 1
    k = _key(url, params, asof)
    p = _path(k)
    if ON:
        try:
            with open(p, encoding="utf-8") as f:
                rec = json.load(f) or {}
            if rec.get("neg"):
                if (time.time() - float(rec.get("at") or 0)) <= NEG_TTL:
                    _STATS["neg_hit"] += 1
                    return None
            else:
                _STATS["hit"] += 1
                return rec.get("data")
        except FileNotFoundError as _e_sil1:
            _silent_alert("http_cache.py:76", _e_sil1)
        except Exception:
            _STATS["err"] += 1
    _STATS["miss"] += 1
    to = float(TIMEOUT if timeout is None else min(float(timeout), TIMEOUT))
    try:
        import requests
        import urllib3
        urllib3.disable_warnings()
        r = requests.get(url, params=params or {}, headers=headers or {}, verify=verify, timeout=to)
        data = r.json()
    except Exception as e:
        if "timeout" in str(e).lower() or "timed out" in str(e).lower():
            _STATS["saved_timeout"] += 1
        data = None
    if ON:
        try:
            os.makedirs(CACHE, exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"data": data, "neg": data is None,
                           "at": time.time(), "url": url, "params": params or {},
                           "asof": asof, "timeout": to}, f, ensure_ascii=False)
            os.replace(tmp, p)
        except Exception:
            _STATS["err"] += 1
    if data is None and allow_negative:
        sig = url
        if sig not in _LOGGED:
            _LOGGED.add(sig)
            print("[http-cache] 取数失败（已落负缓存 %.0fs，本日不再重试）: %s" % (NEG_TTL, url), flush=True)
    return data
