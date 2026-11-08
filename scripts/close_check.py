#!/usr/bin/env python3
"""close_check.py — 收尾检查：任何一项不过，就不许说"完成"

为什么需要它：
    这个项目过去所有"完成"都是自我声明。规则写在文档里，但没有任何东西会失败，
    所以模型（和人）都会漂移到最省事的那条路：写 JSON 而不写 PG、scp 而不 push、
    硬编码而不抽象。**只有会失败的检查才能真正改变默认行为。**

检查项：
    1. 工作区干净          git status --porcelain 为空
    2. 代码已进部署仓库    HEAD 不领先 srv/main
    3. PG 可用            DATABASE_URL 能连上（隧道断了就红）
    4. 持久化无回归        scripts/lint_persist.py 通过
    5. 线上检出同步        仅警告：prod HEAD 与本地不一致时提示 pull

用法：
    .venv/bin/python scripts/close_check.py
    .venv/bin/python scripts/close_check.py --skip-ssh   # 离线时跳过第 5 项
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY_REMOTE = "srv"
PROD_DIR = "/opt/marcus-platform"
SSH_HOST = "marcusrv"

FAILED: list[str] = []
WARNED: list[str] = []


def run(cmd: list[str], cwd: str = ROOT, timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except FileNotFoundError as e:
        return 127, str(e)


def check(name: str, ok: bool, detail: str) -> None:
    mark = "✓" if ok else "✗"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def check_worktree() -> None:
    rc, out = run(["git", "status", "--porcelain"])
    lines = [l for l in out.strip().splitlines() if l.strip()]
    if rc != 0:
        check("工作区干净", False, out.strip()[:200])
        return
    if not lines:
        check("工作区干净", True, "")
        return
    sample = "\n        ".join(lines[:6])
    more = f"\n        …另有 {len(lines) - 6} 项" if len(lines) > 6 else ""
    check("工作区干净", False,
          f"{len(lines)} 项未提交：\n        {sample}{more}\n"
          "        → 要么提交进 git，要么写进 .gitignore，要么删掉；不许留在工作区里算完成")


def check_pushed() -> None:
    rc, out = run(["git", "rev-parse", "--verify", f"{DEPLOY_REMOTE}/main"])
    if rc != 0:
        check("代码已进部署仓库", False, f"找不到 {DEPLOY_REMOTE}/main；先 `git push {DEPLOY_REMOTE} main`")
        return
    rc, out = run(["git", "rev-list", "--count", f"{DEPLOY_REMOTE}/main..HEAD"])
    if rc != 0:
        check("代码已进部署仓库", False, out.strip()[:200])
        return
    ahead = int((out.strip() or "0").splitlines()[0])
    if ahead == 0:
        check("代码已进部署仓库", True, f"HEAD == {DEPLOY_REMOTE}/main")
    else:
        rc2, log = run(["git", "log", "--oneline", f"{DEPLOY_REMOTE}/main..HEAD", "-5"])
        check("代码已进部署仓库", False,
              f"领先 {DEPLOY_REMOTE}/main {ahead} 个提交：\n        "
              + log.strip().replace("\n", "\n        ")
              + f"\n        → `git push {DEPLOY_REMOTE} main`")


def check_pg() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(os.path.join(ROOT, ".env"), override=False)
    except Exception:
        pass
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        check("PG 可用", False, "环境里没有 DATABASE_URL（检查 .env）")
        return
    shown = dsn
    if "@" in dsn:
        head, tail = dsn.split("@", 1)
        shown = head.split("://")[0] + "://***@" + tail
    try:
        import psycopg2  # type: ignore
    except ImportError:
        check("PG 可用", False, "psycopg2 不可用，用 .venv/bin/python 跑本脚本")
        return
    try:
        conn = psycopg2.connect(dsn, connect_timeout=8)
        cur = conn.cursor()
        cur.execute("select count(*) from wave_state")
        rows = cur.fetchone()[0]
        conn.close()
        check("PG 可用", True, f"{shown}（wave_state {rows} 行）")
    except Exception as e:
        msg = str(e).strip().splitlines()[0][:160]
        check("PG 可用", False,
              f"{shown} 连不上：{msg}\n"
              "        → 大概率是隧道断了，跑 ./scripts/pg_tunnel.sh status（或 start）。"
              "**不要写 JSON 兜底。**")


def check_persist() -> None:
    rc, out = run([sys.executable, os.path.join(ROOT, "scripts", "lint_persist.py")])
    text = out.strip()
    ok = rc == 0
    detail = "" if ok else text[:1200]
    if ok:
        # 只保留最后一行摘要
        detail = text.splitlines()[-1] if text else ""
    check("持久化无回归", ok, detail)


def check_prod_sync() -> None:
    rc, head = run(["git", "rev-parse", "HEAD"], timeout=10)
    local = head.strip()
    rc, out = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", SSH_HOST,
                   f"git -C {PROD_DIR} rev-parse HEAD"], timeout=20)
    if rc != 0:
        WARNED.append(f"线上检出未检查（ssh 失败）：{out.strip()[:120]}")
        print(f"  [!] 线上检出同步 — 跳过（ssh 失败，可加 --skip-ssh）")
        return
    prod = out.strip().splitlines()[-1] if out.strip() else ""
    if prod == local:
        print("  [✓] 线上检出同步 — prod HEAD == 本地 HEAD")
    else:
        WARNED.append(f"线上检出 {prod[:8]} ≠ 本地 {local[:8]}，需要时执行 git -C {PROD_DIR} pull self")
        print(f"  [!] 线上检出不同步 — prod {prod[:8]} vs 本地 {local[:8]}"
              f"（部署时：git -C {PROD_DIR} pull self）")


def main() -> int:
    ap = argparse.ArgumentParser(description="收尾检查")
    ap.add_argument("--skip-ssh", action="store_true", help="跳过线上检出检查")
    args = ap.parse_args()

    print("== marcus 收尾检查 ==")
    check_worktree()
    check_pushed()
    check_pg()
    check_persist()
    if not args.skip_ssh:
        check_prod_sync()

    print()
    if FAILED:
        print(f"结果：不通过（{len(FAILED)} 项失败）→ 不许说\"完成\"")
        for f in FAILED:
            print(f"  - {f}")
        if WARNED:
            print("警告：")
            for w in WARNED:
                print(f"  - {w}")
        return 1
    print("结果：通过 ✓")
    if WARNED:
        print("警告：")
        for w in WARNED:
            print(f"  - {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
