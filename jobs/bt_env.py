# -*- coding: utf-8 -*-
"""bt_env.py — 让回测工具**同时能在生产容器（/app 布局）和本地仓库（repo 布局）里跑**。

生产容器是"挂载式"布局：`/app/jobs`、`/app/apps/main_line`、`/app/app`（= backend/app）、`/app/data`；
本地仓库是 `/home/.../marcus-platform/{jobs,apps/main_line,backend/app,data}`。
以前工具里写死了 `/app/...` → 只能在容器里跑（想在本地跑就得改一堆路径）。这里统一：

    import bt_env
    bt_env.add_paths()          # 把两个布局的候选目录都塞进 sys.path（幂等）
    REPO / DATA / JOBS / LOGS   # 路径常量（DATA 仍以 DATA_DIR 环境变量优先）

注意：`DATA` 优先取 `DATA_DIR`（沙箱/回测根目录都靠它），`LOGS` 优先取生产 `logs/`，其次取
`<DATA>/_bt_full/_sched`（本地把调度日志同步到这里）。
"""
from __future__ import annotations

import os
import sys

JOBS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(JOBS)
DATA = os.environ.get("DATA_DIR") or os.path.join(REPO, "data")


def _first_dir(*cands):
    for c in cands:
        if c and os.path.isdir(c):
            return c
    return cands[0] if cands else ""


def _pick_logs():
    """优先选**真的有 `scheduler_*.jsonl`** 的目录：本地常常只有 `logs/` 的空壳子目录，
    而调度日志是同步到 `<DATA>/_bt_full/_sched/` 的。"""
    import glob as _g
    for c in (os.path.join(DATA, "_bt_full", "_sched"), os.path.join(REPO, "logs")):
        try:
            if _g.glob(os.path.join(c, "scheduler_*.jsonl")):
                return c
        except Exception:
            pass
    return _first_dir(os.path.join(REPO, "logs"), os.path.join(DATA, "_bt_full", "_sched"))


LOGS = _pick_logs()


def add_paths(extra=()):
    """把可 import 的根目录塞进 sys.path（容器布局与仓库布局都覆盖）。"""
    cands = [
        REPO,
        os.path.join(REPO, "apps", "main_line"),
        JOBS,
        os.path.join(REPO, "core"),
        os.path.join(REPO, "backend"),
        os.path.join(REPO, "backend", "app"),
        os.path.join(REPO, "app"),                 # 容器里 backend/app 挂在 /app/app
        "/app", "/app/apps/main_line", "/app/jobs", "/app/core", "/app/app",
    ]
    cands.extend(extra)
    for p in cands:
        if p and os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)


def jobs_file(name: str) -> str:
    """`jobs/` 下的脚本绝对路径（容器与本地都指向同一套工具）。"""
    return os.path.join(JOBS, name)


def data_file(*parts) -> str:
    return os.path.join(DATA, *parts)
