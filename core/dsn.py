# -*- coding: utf-8 -*-
"""core/dsn.py — PostgreSQL 连接串的唯一来源

为什么要有这个文件：
    全仓曾有 163 处硬编码 DSN（`postgresql://marcus:marcus123@127.0.0.1:<port>/...`），
    端口散落在三个不同的库上（18789 线上隧道 / 5432 本地库 / 5433 回测库）。
    结果是同一个脚本在这儿能跑、在那儿连错库，而且口令进了 git。

规则（与 core/qq_notifier.py 2026-09-26 修定的口径一致）：
    1. **进程环境变量优先** —— 线上容器注入的 DATABASE_URL（postgres:5432）不许被仓库 .env 覆盖；
    2. 其次读仓库根 `.env`（本地开发用，已被 .gitignore 排除）；
    3. 都没有 → **明确抛错**，不许静默回退到某个硬编码默认值。

    代码里不允许再出现用户名 / 口令 / 端口字面量。

用法：
    from core import dsn
    conn = psycopg2.connect(dsn.main())        # 主库（线上）
    conn = psycopg2.connect(dsn.backtest())    # 回测库
    print(dsn.mask(dsn.main()))                # 打日志用，口令已隐去
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

# 主库：优先级从高到低
_MAIN_KEYS = ("DATABASE_URL", "PG_DSN", "MARCUS_DATABASE_URL")
# 回测库（名字按仓库里实际用过的都收进来，避免设了变量却没人认）
_BT_KEYS = ("BT_DATABASE_URL", "BT_PG_URL", "BT_PG_DSN", "PG_BT_DSN", "BT_DSN")

_loaded = False


def _load_env_file() -> None:
    """把仓库根 .env 灌进 os.environ，但**不覆盖**已存在的变量。"""
    global _loaded
    if _loaded:
        return
    _loaded = True
    if not ENV_FILE.exists():
        return
    try:
        for raw in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:      # 已注入的不覆盖
                os.environ[key] = val
    except OSError:
        pass


def _resolve(keys: tuple[str, ...], kind: str) -> str:
    _load_env_file()
    for key in keys:
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    hint = (
        f"      本地：在 {ENV_FILE} 里补 {keys[0]}=...，并确认 `./scripts/pg_tunnel.sh status` 是通的。\n"
        if kind == "主库"
        else f"      本地：在 {ENV_FILE} 里补 {keys[0]}=...（回测库是 bt_pg 容器，宿主机端口 5433）。\n"
    )
    raise RuntimeError(
        f"[dsn] 找不到{kind}连接串。已查找环境变量 {', '.join(keys)}，以及 {ENV_FILE}。\n"
        + hint
        + f"      线上：确认容器注入了对应环境变量。\n"
        + f"      **不要在这里加硬编码默认值**——那正是本次收口要消灭的东西。"
    )


def main() -> str:
    """主库连接串（线上库）。环境变量 DATABASE_URL 优先。"""
    return _resolve(_MAIN_KEYS, "主库")


def backtest() -> str:
    """回测库连接串（bt_pg，端口 5433）。"""
    return _resolve(_BT_KEYS, "回测库")


def mask(dsn: str) -> str:
    """隐去口令，用于日志/报错。"""
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", dsn or "")


def host_port(dsn: str) -> str:
    """只取 host:port，用于日志里区分连的是哪个库。"""
    m = re.search(r"@([^/]+)/", dsn or "")
    return m.group(1) if m else "?"


__all__ = ["main", "backtest", "mask", "host_port", "ROOT", "ENV_FILE"]
