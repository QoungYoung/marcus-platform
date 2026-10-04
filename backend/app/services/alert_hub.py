# -*- coding: utf-8 -*-
"""**统一异常告警出口**（用户 2026-09-26「加个全局异常处理，统一走QQ推送」✓；账本 §9.238）。

背景（真实教训 ✓）：埋伏纪律每次抛 `No module named 'PySide6'` ✗，
  而监控主循环只 `print("[TMonitor] 本轮异常: …")` ✗ ⇒ **没有任何告警** ✗ ⇒
  **整条纪律静默失效很久才被发现** ✗✗（用户问「抛异常怎么没QQ通知我」✓）

设计（三条 ✓）：
  1. **全局兜底**：`sys.excepthook`（主线程）＋ `threading.excepthook`（**子线程** ✓ —— 监控线程正是这里 ✓）
  2. **统一出口** `note(where, exc=None, msg="", level="error")` ✓：
     · **必落盘** `<DATA_DIR>/alerts.jsonl` ✓（复盘可查 ✓，**不依赖 QQ** ✓）
     · 可选**推 QQ** ✓（`WOLF_ALERT_QQ=1` ＋ `WOLF_ALERT_QQ_TO=<openid|group>` ✓）
  3. **防刷屏** ✓：同一条（where+异常类型+前 60 字）**N 秒内只推一次** ✓（`WOLF_ALERT_DEDUP_SEC`，默认 600 ✓），
     且有全局限流（`WOLF_ALERT_MAX_PER_HOUR`，默认 20 ✓）—— 重复异常**只落盘、不再推** ✓

开关（**库内默认关** ✓ ⇒ 生产零影响 ✓）：
  · `WOLF_ALERT_HUB=1` ⇒ 安装全局 hook ✓
  · `WOLF_ALERT_QQ=1` ⇒ 允许推送（关 ⇒ **只落盘** ✓）
"""
from __future__ import annotations

import json
import os
import sys
import threading
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


_LOCK = threading.Lock()
_SEEN: Dict[str, float] = {}
_SENT_TS: list = []
_LAST_BODY: str = ""


def _env_on(name: str, dflt: str = "0") -> bool:
    return str(os.getenv(name, dflt) or dflt).strip().lower() in ("1", "true", "yes", "on")


def _i(name: str, dflt: int) -> int:
    try:
        return int(float(os.getenv(name, str(dflt)) or dflt))
    except Exception:
        return dflt


def _alerts_path() -> str:
    d = os.environ.get("DATA_DIR", "/app/data")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as _e_sil1:
        _silent_alert("alert_hub.py:51", _e_sil1)
    return os.path.join(d, "alerts.jsonl")


def _fmt(where: str, exc: Optional[BaseException], msg: str = "") -> str:
    _t = type(exc).__name__ if exc is not None else ""
    _m = str(exc)[:200] if exc is not None else str(msg)[:200]
    return "[ALERT] %s | %s%s" % (where, _t, (": " + _m) if _m else "")


def _dedup_key(where: str, exc: Optional[BaseException], msg: str) -> str:
    return "%s|%s|%s" % (where, type(exc).__name__ if exc is not None else "", (str(exc) if exc is not None else msg)[:60])


def push_qq(text: str) -> bool:
    """把一行文本推到 QQ（失败**只记不抛** ✓；收件人取 `WOLF_ALERT_QQ_TO` ✓）。"""
    # ⚠️⚠️ 2026-09-26 **重大坑**（账本 §9.240）：`core/qq_notifier.py` 顶层 `_load_env()`
    #   会**强制覆盖 `DATABASE_URL`**（改成 .env 里的 18789 ✗ —— AGENTS.md 早有警告 ✓）
    #   ⇒ 只要推一次告警，**本进程后续所有数据库连接全废** ✗✗
    #   （实测症状：`[TMonitor] 持仓读取失败 … port 18789 failed` ⇒ **0 成交** ✗）
    #   ⇒ 这里**保存并还原**（`finally` 里也还原 ✓），把副作用**完全隔离**在本函数内 ✓
    _dsn_bak = os.environ.get("DATABASE_URL")
    try:
        # 收件人优先级：`WOLF_ALERT_QQ_TO` → **`QQ_BOT_RECIPIENT`**（仓库既有的默认收件人 ✓）
        to = str(os.getenv("WOLF_ALERT_QQ_TO", "") or os.getenv("QQ_BOT_RECIPIENT", "") or "").strip()
        if not to:
            return False
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core"))
        try:
            from qq_notifier import send_c2c_message  # type: ignore
        except Exception:
            import importlib.util as _ilu
            _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core", "qq_notifier.py")
            _sp = _ilu.spec_from_file_location("qq_notifier", os.path.abspath(_p))
            _m = _ilu.module_from_spec(_sp)  # type: ignore
            _sp.loader.exec_module(_m)  # type: ignore
            send_c2c_message = _m.send_c2c_message  # type: ignore
        return bool(send_c2c_message(to, str(text)[:900]))
    except Exception:
        return False
    finally:
        try:
            if _dsn_bak is not None and os.environ.get("DATABASE_URL") != _dsn_bak:
                os.environ["DATABASE_URL"] = _dsn_bak     # **还原** ✗ ⇒ 不再污染本进程 ✓
        except Exception as _e_sil2:
            _silent_alert("alert_hub.py:96", _e_sil2)


def note(where: str, exc: Optional[BaseException] = None, msg: str = "", ctx: Optional[Dict[str, Any]] = None) -> None:
    """**统一告警出口** ✓：必落盘 ✓；按开关与去重/限流决定是否推 QQ ✓；**绝不抛** ✓。"""
    global _LAST_BODY
    try:
        line = _fmt(where, exc, msg)
        if ctx:
            line += " | ctx=" + json.dumps(ctx, ensure_ascii=False)[:200]
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "where": where,
               "exc": (type(exc).__name__ if exc is not None else ""),
               "msg": (str(exc)[:300] if exc is not None else str(msg)[:300]),
               "ctx": (ctx or {})}
        try:
            with open(_alerts_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as _e_sil3:
            _silent_alert("alert_hub.py:114", _e_sil3)
        print(line, flush=True)
        if not _env_on("WOLF_ALERT_QQ", "0"):
            return
        now = time.time()
        key = _dedup_key(where, exc, msg)
        with _LOCK:
            if now - float(_SEEN.get(key, 0) or 0) < _i("WOLF_ALERT_DEDUP_SEC", 600):
                return
            _SEEN[key] = now
            _cut = now - 3600
            while _SENT_TS and _SENT_TS[0] < _cut:
                _SENT_TS.pop(0)
            if len(_SENT_TS) >= _i("WOLF_ALERT_MAX_PER_HOUR", 20):
                return
            _SENT_TS.append(now)
            _LAST_BODY = line
        push_qq(line)
    except Exception as _e_sil4:
        _silent_alert("alert_hub.py:133", _e_sil4)


def note_silent(where: str, exc: Optional[BaseException] = None, msg: str = "") -> None:
    """**静默点专用**出口（账本 §9.543）：原来 `except …: pass` ⇒ 现在至少留痕。

    与 `note()` 一样：**绝不抛** ✓；QQ 仍受去重(600s)/限流(20 条/小时)约束 ⇒ **不会刷屏** ✓。
    """
    try:
        note("silent:" + str(where), exc, msg)
    except Exception as _e_sil5:
        _silent_alert("alert_hub.py:144", _e_sil5)


def install() -> bool:
    """安装**全局异常钩子**（主线程 ＋ **子线程** ✓）—— 幂等 ✓。"""
    if not _env_on("WOLF_ALERT_HUB", "0"):
        return False
    if getattr(install, "_done", False):
        return True
    _prev = sys.excepthook

    def _hook(etype, value, tb):
        try:
            note("sys.excepthook", value if value is not None else Exception(str(etype)), "")
        except Exception as _e_sil6:
            _silent_alert("alert_hub.py:159", _e_sil6)
        try:
            _prev(etype, value, tb)
        except Exception as _e_sil7:
            _silent_alert("alert_hub.py:163", _e_sil7)

    sys.excepthook = _hook  # type: ignore
    try:
        _prev_th = getattr(threading, "excepthook", None)

        def _thook(args):  # type: ignore
            try:
                note("threading.excepthook[%s]" % getattr(args.thread, "name", "?"),
                     getattr(args, "exc_value", None), "")
            except Exception as _e_sil8:
                _silent_alert("alert_hub.py:174", _e_sil8)
            try:
                if _prev_th:
                    _prev_th(args)  # type: ignore
            except Exception as _e_sil9:
                _silent_alert("alert_hub.py:179", _e_sil9)

        threading.excepthook = _thook  # type: ignore
    except Exception as _e_sil10:
        _silent_alert("alert_hub.py:183", _e_sil10)
    install._done = True  # type: ignore
    note("alert_hub", None, "全局异常钩子已安装 ✓（QQ 推送=%s）" % ("开" if _env_on("WOLF_ALERT_QQ", "0") else "关（只落盘）"))
    return True
