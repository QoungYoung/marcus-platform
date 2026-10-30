# -*- coding: utf-8 -*-
"""chain_util.py — 顺序执行子脚本，单步失败不阻断后续。"""
from __future__ import annotations
import os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _child_env():
    """子进程的 PYTHONPATH 兜底：脚本直跑时 sys.path[0] 只有**脚本目录**，`from app.…` 找不到包
    （容器里 `app` 包 = /app/app，本地 = backend/app）⇒ 链步骤静默 fail-open ✗（2026-10-30 08:10
    `浪型状态` 即 ModuleNotFoundError: No module named 'app' ✗）。cwd 不进 sys.path，改环境变量最稳 ✓。"""
    env = dict(os.environ)
    old = (env.get("PYTHONPATH") or "").strip()
    paths = [p for p in (ROOT, os.path.join(ROOT, "backend")) if os.path.isdir(p)]
    if old:
        paths.append(old)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


def run_steps(steps):
    bad = []
    for label, path, args in steps:
        cmd = [sys.executable, os.path.join(ROOT, path)] + list(args or [])
        t0 = time.time()
        print(f"[chain] ▶ {label}: {' '.join(cmd)}", flush=True)
        try:
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=900,
                               env=_child_env())
            out = (r.stdout or "") + (r.stderr or "")
            print(out[-3000:], flush=True)
            print(f"[chain] ◀ {label} rc={r.returncode} {time.time()-t0:.1f}s", flush=True)
            if r.returncode != 0:
                bad.append(label)
        except Exception as e:
            print(f"[chain] ✗ {label}: {type(e).__name__}: {str(e)[:200]}", flush=True)
            bad.append(label)
    print(f"[chain] 汇总: {len(steps)} 步，失败 {len(bad)} 步" + (f"：{', '.join(bad)}" if bad else " ✓"), flush=True)
    return 1 if bad else 0
