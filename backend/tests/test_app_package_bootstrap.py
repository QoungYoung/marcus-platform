# -*- coding: utf-8 -*-
"""回归：`import app` 必须自己把 <项目根>/core 挂上 sys.path。

事故背景（2026-09-17/18 生产）：
`app/api/portfolio.py`、`app/services/t_gateway.py` 等模块**顶层** `from trade_direction import ...`，
而 `trade_direction.py` 在 `<项目根>/core/`。此前只有 `app/main.py`（API）与 `app/worker_main.py`
（worker）引导 sys.path；调度器用**子进程**直跑的入口脚本（`scripts/snapshot_portfolio.py`、
`jobs/record_orderbook.py`、`jobs/recon_account_cash.py`）不经过这两个入口，于是
`daily_snapshot` 09-17/09-18 连续失败、`orderbook_snapshot_r9` 09-18 盘中每 5 分钟失败一次。

⚠️ 这个 bug **在测试进程里永远不复现**（同进程早已把 core 加进 sys.path），
所以必须用「干净子进程 + 只给 backend 前缀 + 不带 PYTHONPATH」复现真实入口条件。
"""
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
CORE = REPO_ROOT / "core"


def _run_isolated(code: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )


def test_import_app_bootstraps_core_path():
    """只把 backend 放进 sys.path（入口脚本的真实做法）时，core/ 也必须可用。"""
    code = (
        "import sys;"
        f"sys.path.insert(0, {str(BACKEND)!r});"
        f"assert {str(CORE)!r} not in sys.path, '前置条件失败：core 已在 path 上';"
        "import app;"  # 触发器：包初始化负责引导
        f"assert {str(CORE)!r} in sys.path, 'import app 没有把 core/ 加进 sys.path';"
        "import trade_direction;"  # 真正的失败点在 app.api.portfolio / t_gateway 的顶层导入
        "print('BOOTSTRAP_OK', trade_direction.__file__)"
    )
    p = _run_isolated(code)
    assert p.returncode == 0, f"子进程失败:\nSTDOUT:{p.stdout}\nSTDERR:{p.stderr}"
    assert "BOOTSTRAP_OK" in p.stdout, p.stdout


def test_failing_entrypoint_script_imports_clean():
    """真实入口脚本 `scripts/snapshot_portfolio.py` 在干净子进程里必须能 import 通过。

    用 `--help`：argparse 会在 argparse 阶段退出（退出码 0），不会触碰数据库/行情，
    但**模块级 import** 已经完整执行过 —— 正是事故里失败的那一步。
    """
    script = REPO_ROOT / "scripts" / "snapshot_portfolio.py"
    assert script.exists(), f"入口脚本不存在: {script}"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    p = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300, env=env,
    )
    assert "ModuleNotFoundError" not in p.stderr, p.stderr
    assert p.returncode == 0, f"rc={p.returncode}\nSTDOUT:{p.stdout}\nSTDERR:{p.stderr}"
