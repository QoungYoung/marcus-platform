# -*- coding: utf-8 -*-
"""
Marcus Platform Backend

⚠️ 本文件不得依赖 app.* 内部模块（它是包初始化的第一步）。
"""
import sys
from pathlib import Path

__version__ = "1.0.0"


def _bootstrap_paths() -> None:
    """把 <项目根> 与 <项目根>/core 挂上 sys.path（幂等）。

    为什么必须在这里做（2026-09-18 事故复盘）：
    `app/api/portfolio.py`、`app/services/t_gateway.py`、`app/core/trading/*` 等模块在
    **顶层** import `trade_direction`（统一方向词表），而 `trade_direction.py` 位于
    `<项目根>/core/`，不在 backend 包内。在此之前只有两个入口会引导 sys.path：
        · app/main.py（API 进程）
        · app/worker_main.py（worker 进程）
    被调度器以**子进程**方式直跑的入口脚本（`scripts/snapshot_portfolio.py`、
    `jobs/record_orderbook.py`、`jobs/recon_account_cash.py`、apps/… 等）不经过这两个
    入口，各自只往 sys.path 里放项目根/backend —— 于是 2026-09-16 的语料统一提交把
    `trade_direction` 引进 `app/api/portfolio.py` 之后，这些任务的子进程齐刷刷死在
    `ModuleNotFoundError: No module named 'trade_direction'`：
        · 每日净值快照 daily_snapshot：09-17、09-18 连续两天 15:01 失败
        · orderbook_snapshot_r9：09-18 盘中每 5 分钟失败一次（取持仓失败 → 无标的可记）
        · account_cash_recon：09-18 15:05 失败
    放在包初始化里 = 任何 `import app.*` 的进程（含子进程入口脚本）都先拿到 core/，
    不依赖各入口脚本是否记得自己引导路径。

    探测逻辑与 app/main.py 保持一致：不能只用 `core/__init__.py` 判断，因为
    `backend/app/core/__init__.py` 也会命中；用 `core/utils/trade_day_utils.py` 定位真正的
    项目根，兼容本地（backend/app/…）与 Docker（/app/app/…）两种布局。
    """
    platform_root = Path(__file__).resolve().parent
    for _ in range(5):
        if (platform_root / "core" / "utils" / "trade_day_utils.py").exists():
            break
        platform_root = platform_root.parent
    # core 放在 platform_root 之前（与 main.py 顺序一致）：优先命中 core/ 下的顶层模块
    for _p in (platform_root, platform_root / "core"):
        if _p.is_dir() and str(_p) not in sys.path:
            sys.path.insert(0, str(_p))


_bootstrap_paths()
