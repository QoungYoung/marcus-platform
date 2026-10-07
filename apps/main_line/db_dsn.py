# -*- coding: utf-8 -*-
"""统一 DSN 解析（2026-09-19 修「postgres 主机名 + .env 端口不对」的取数失败）。

背景（实测日志）：`[theme_volfund] 交易日取数失败: could not translate host name "postgres"` ——
不少 main_line 模块的 DSN 兜底写的是 **docker 主机名 postgres**（容器内对、本机/回放宿主上必错），
而 repo `.env` 里那条 `127.0.0.1:18789` 在本机也不是回放用的库（回放用 `127.0.0.1:5433`）。
于是这些模块每次都白连一次、打一行失败日志（还可能是布腿器变慢的来源之一）。

解析顺序（每个都做 1s 探活，结果进程内缓存）：
  ① 环境变量 DATABASE_URL（回放/生产显式注入，永远优先）
  ② repo `.env` 的 DATABASE_URL
  ③ 本机回放/开发库 `127.0.0.1:5433`
  ④ docker 主机名 `postgres:5432`（容器内）
全部探活失败时返回第一个非空候选（fail-open，调用方照原样报错/降级）。
"""
from __future__ import annotations

import os
from typing import Optional


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


_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LOCAL = "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"
_DOCKER = "postgresql://marcus:marcus123@postgres:5432/marcus_trading"
_CACHE: dict = {}


def _env_file_dsn() -> Optional[str]:
    try:
        with open(os.path.join(_REPO, ".env"), encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln.startswith("DATABASE_URL="):
                    return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception as _e_sil1:
        _silent_alert("db_dsn.py:35", _e_sil1)
    return None


def _probe(dsn: str) -> bool:
    if not dsn:
        return False
    if dsn in _CACHE:
        return bool(_CACHE[dsn])
    ok = {"v": False}

    def _try():
        try:
            import psycopg2
            c = psycopg2.connect(dsn, connect_timeout=1)
            c.close()
            ok["v"] = True
        except Exception:
            ok["v"] = False
    try:
        import threading
        th = threading.Thread(target=_try, daemon=True)
        th.start()
        th.join(2.5)          # ⚠️ psycopg2 的 connect_timeout 不覆盖 **DNS 解析**：
        #   主机名 postgres 在本机解析会挂很久 ⇒ 必须用线程硬上限兜住，否则布腿器被它拖住。
    except Exception:
        ok["v"] = False
    _CACHE[dsn] = ok["v"]
    return ok["v"]


def candidates() -> list:
    out = [os.getenv("DATABASE_URL"), _env_file_dsn(), _LOCAL, _DOCKER]
    seen, res = set(), []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            res.append(c)
    return res


def dsn() -> str:
    """返回可用的 DSN（探活优先；全失败返回第一个候选）。"""
    cands = candidates()
    for c in cands:
        if _probe(c):
            return c
    return cands[0] if cands else _LOCAL


def backend_on_path() -> None:
    """把 backend/ 加进 sys.path（修 `No module named 'app'` 这类导入失败）。"""
    import sys
    p = os.path.join(_REPO, "backend")
    if p not in sys.path:
        sys.path.insert(0, p)
