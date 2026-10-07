# -*- coding: utf-8 -*-
"""安全停臂（账本 §9.741）—— 只停**本臂自己的进程树**，绝不误伤别的进程

为什么必须这样写 ✗（今晚踩了 6 次 ✓）：
  · `pgrep -f` / `pkill -f` 的模式会**匹配到调用者自己的命令行** ✗
    ⇒ 实测把自己 SIGTERM 掉 6 次 ✓（命令里含 `bt_dashboard` / `bt_days` 字样 ✓）
  · 而且"停 driver"不等于"停子进程" ✗ —— `bt_prod_run` / `bt_run_pinned` 等会变**孤儿** ✓
    ⇒ 曾与新跑批**共用同一沙箱** ✗（污染 ✓）

本脚本的做法 ✓：
  ① 遍历 `/proc/<pid>/cmdline`（不是 grep ✓），找 cmdline 同时含 `--root <root>` 与已知脚本名者；
  ② **显式排除**自身与自己的祖先链（ppid 上溯 ✓）⇒ 永不自杀 ✓；
  ③ 先杀**最深的后代**（按 ppid 深度倒序 ✓），再杀父 ✓ ⇒ 不留孤儿 ✓；
  ④ 默认 `--dry-run` 只打印；真正执行要显式 `--yes` ✓。

用法 ✓：
  .venv/bin/python jobs/bt_stop_arm.py --root data/_bt_t35d            # 只列（默认 dry-run ✓）
  .venv/bin/python jobs/bt_stop_arm.py --root data/_bt_t35d --yes      # 真停
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from typing import Dict, List, Set, Tuple

# 只认这些"跑批侧"脚本 ✓（避免把看板/转发器/看门狗算进来 ✗ —— 它们另有看门狗 ✓）
ARM_SCRIPTS = (
    "bt_days.py", "bt_prod_run.py", "bt_run_pinned.py", "bt_day_legs.py",
    "bt_day_legs_switch.py", "bt_llm_replay.py", "bt_agent_loop.py",
    "bt_asof_fetch.py", "bt_local_pro.py", "bt_pack_mins.py", "bt_fetch_mins.py",
    "bt_seed_day.py", "bt_backfill_mins_union.py",
)


def _ancestors() -> Set[int]:
    """自身 + 祖先链的 pid（这些**绝不能杀** ✓）"""
    keep: Set[int] = set()
    pid = os.getpid()
    for _ in range(32):
        keep.add(pid)
        try:
            with open("/proc/%d/stat" % pid, "rb") as fh:
                parts = fh.read().decode("utf-8", "replace").rsplit(")", 1)[-1].split()
            pid = int(parts[1])          # stat 里第 2 个字段（去掉 comm 后）就是 ppid ✓
        except Exception:
            break
        if pid <= 1:
            break
    return keep


def _read(pid: int, name: str) -> str:
    try:
        with open("/proc/%d/%s" % (pid, name), "rb") as fh:
            return fh.read().decode("utf-8", "replace")
    except Exception:
        return ""


def scan(root: str) -> List[Tuple[int, int, str]]:
    """返回 [(pid, ppid, cmdline)]，仅含本臂的跑批进程 ✓"""
    root_abs = os.path.abspath(root)
    out: List[Tuple[int, int, str]] = []
    for entry in sorted(os.listdir("/proc")):
        if not entry.isdigit():
            continue
        pid = int(entry)
        cmd = _read(pid, "cmdline").replace("\x00", " ").strip()
        if not cmd:
            continue
        if not any(s in cmd for s in ARM_SCRIPTS):
            continue
        # 必须指向**本臂的 root** ✓（不同臂互不干扰 ✓）
        if root_abs not in cmd and root not in cmd:
            continue
        st = _read(pid, "stat").rsplit(")", 1)[-1].split()
        ppid = int(st[1]) if len(st) > 1 else 0
        out.append((pid, ppid, cmd))
    return out


def depth_of(pid: int, parents: Dict[int, int]) -> int:
    d, cur = 0, pid
    while cur in parents and d < 64:
        cur = parents[cur]
        d += 1
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description="安全停臂（只停本臂进程树 ✓）")
    ap.add_argument("--root", default="data/_bt_t35d", help="本臂沙箱根（用于精确匹配 ✓）")
    ap.add_argument("--yes", action="store_true", help="真正执行（默认只打印 ✓）")
    ap.add_argument("--wait", type=float, default=3.0, help="每步等待秒数")
    a = ap.parse_args()

    keep = _ancestors()
    procs = scan(a.root)
    print("  本臂跑批进程 ⇒ %d 个" % len(procs))
    for pid, ppid, cmd in procs:
        tag = "  ★自身/祖先（跳过 ✓）" if pid in keep else ""
        print("     pid=%-8s ppid=%-8s %s%s" % (pid, ppid, cmd[:96], tag))
    targets = [p for p in procs if p[0] not in keep]
    if not targets:
        print("  ⇒ 没有可停的进程（或都属自身链 ✓）")
        return 0
    if not a.yes:
        print("  （dry-run ✓ 未执行；要真停请加 --yes ✓）")
        return 0

    parents = {pid: ppid for pid, ppid, _ in procs}
    targets.sort(key=lambda t: depth_of(t[0], parents), reverse=True)
    # 先温和
    for pid, _ppid, cmd in targets:
        try:
            os.kill(pid, signal.SIGTERM)
            print("  SIGTERM ⇒ pid=%s（%s）" % (pid, cmd[:72]))
        except ProcessLookupError:
            print("  已不在 ⇒ pid=%s" % pid)
        except Exception as exc:  # noqa: BLE001
            print("  ✗ pid=%s: %s" % (pid, str(exc)[:70]))
    time.sleep(a.wait)
    # 复查，顽固的再 SIGKILL ✓
    left = [(pid, ppid, cmd) for pid, ppid, cmd in targets
            if os.path.exists("/proc/%d" % pid)]
    for pid, _ppid, cmd in left:
        try:
            os.kill(pid, signal.SIGKILL)
            print("  SIGKILL ⇒ pid=%s（残留 ✓）" % pid)
        except Exception:
            pass
    time.sleep(1.0)
    rest = [pid for pid, _p, _c in targets if os.path.exists("/proc/%d" % pid)]
    print("  ⇒ 结果：目标 %d 个，残留 %d 个 %s" % (len(targets), len(rest), rest or "✓ 已全部停止 ✓"))
    return 0 if not rest else 1


if __name__ == "__main__":
    sys.exit(main())
