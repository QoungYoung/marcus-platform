# -*- coding: utf-8 -*-
"""回归：子脚本直跑时 `from app.…` 必须可用（sys.path 只有脚本目录，cwd 不进 sys.path）。

事故（2026-10-30 08:10 生产「浪型状态+历史」链）：调度器用
`/usr/local/bin/python /app/apps/main_line/wave_ladder.py --write --dated` 起子进程 ⇒
sys.path[0] 只有 `apps/main_line`，`app` 包（容器 /app/app、本地 backend/app）找不到 ⇒
`from app.services.index_daily_store import load_rows` 抛 ModuleNotFoundError ⇒ 取数为空 ⇒
「写失败（fail-open）」、rc=1，通知里只剩一行 fail-open，根因被静默吞掉。

⚠️ 这个 bug **在测试进程里永远不复现**（pytest 本进程早已把 backend 加进 sys.path），
所以两处都用「干净子进程 + 不带 PYTHONPATH + cwd 不在含 `app/` 的目录」复现真实入口条件。
"""
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent

IMPORT_APP = (
    "from app.services.index_daily_store import load_rows; print('OK', load_rows.__module__)"
)


def _clean_env():
    return {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}


def _run(code, env, cwd):
    return subprocess.run(
        [sys.executable, "-c", code], cwd=str(cwd), capture_output=True, text=True,
        timeout=180, env=env,
    )


def test_wave_ladder_bootstraps_app_import(tmp_path):
    """wave_ladder.py 自带 bootstrap ⇒ 以「脚本」身份加载后仍能 import app。"""
    code = (
        "import importlib.util;"
        f"p={str(REPO_ROOT / 'apps' / 'main_line' / 'wave_ladder.py')!r};"
        "s=importlib.util.spec_from_file_location('wl', p);"
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m);"
        "import app.services.index_daily_store as d; print('OK', d.__name__)"
    )
    r = _run(code, _clean_env(), tmp_path)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-2000:])
    assert "OK app.services.index_daily_store" in r.stdout


def test_scheduler_child_env_makes_app_importable(tmp_path):
    """调度子进程 env 带 PYTHONPATH ⇒ 直启脚本能 import app；裸 env 必须复现失败。"""
    sys.path.insert(0, str(BACKEND))
    from app.services.scheduler_service import _child_python_env

    bare = _run(IMPORT_APP, _clean_env(), tmp_path)
    assert bare.returncode != 0
    assert "No module named 'app'" in bare.stdout + bare.stderr

    env = _child_python_env(REPO_ROOT)
    assert str(REPO_ROOT) in env["PYTHONPATH"].split(os.pathsep)
    assert env["PYTHONIOENCODING"] == "utf-8"
    r = _run(IMPORT_APP, env, tmp_path)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-2000:])
    assert "OK app.services.index_daily_store" in r.stdout
