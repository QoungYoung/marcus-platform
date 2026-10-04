# -*- coding: utf-8 -*-
"""test_baseline.py —— 全量测试**基线**（账本 §9.549）

**为什么**：`backend/tests` 有**一批既有失败**（与本次改动无关：下单号 `ORD…` 格式／ASOF 缓存／板块审计… ✗）
  ⇒ 不做基线的话，**每次改动后都无法判断"是不是我改坏的"** ✗（今天为此多花了不少时间 ✓）

用法：
  `.venv/bin/python jobs/test_baseline.py --save`     # 采集并保存基线
  `.venv/bin/python jobs/test_baseline.py --diff`     # 与基线对比：**只报新增失败 / 已修复**
输出：`.dsh-tmp/wolfbt/test_baseline.txt`（每行一个 nodeid ✓，已排序 ✓）
"""
from __future__ import annotations
import os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, ".dsh-tmp", "wolfbt", "test_baseline.txt")
DSN = os.getenv("DATABASE_URL") or "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"


# 相关子集（覆盖本次改动会影响到的模块 ✓）——全量 `backend/tests` 在并发跑臂时要 20+ 分钟 ✗
FAST = ["test_ambush_two_steps.py", "test_tier_state_path.py", "test_wolf_ticket_ban.py",
        "test_board_prefilter.py", "test_clock_source_consistency.py", "test_confirm_order.py",
        "test_stop_index_level.py", "test_sell_base_exempt.py", "test_protect_and_dedup.py",
        "test_wolf_trend_stop.py", "test_sell_floor_enforce.py", "test_volume_gate_local.py",
        "test_zhengt_size_cap.py", "test_build_size_floor.py", "test_zt_day_budget.py",
        "test_target_table.py", "test_index_quote_shim.py", "test_multi_account_paper_infra.py"]


def collect(fast: bool = False) -> set:
    env = dict(os.environ, DATABASE_URL=DSN, PYTHONIOENCODING="utf-8")
    targets = ([os.path.join("backend", "tests", f) for f in FAST
                if os.path.exists(os.path.join(ROOT, "backend", "tests", f))]
               if fast else ["backend/tests"])
    cmd = [os.path.join(ROOT, ".venv", "bin", "python"), "-m", "pytest"] + targets + [
        "-q", "--tb=no", "-p", "no:cacheprovider", "--ignore=backend/tests/test_marcus_trade_notify.py"]
    p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=1800)
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    bad = set()
    for line in out.splitlines():
        m = re.match(r"^(FAILED|ERROR)\s+([^\s]+)", line.strip())
        if m:
            bad.add(m.group(2))
    tail = [l for l in out.splitlines() if re.search(r"\d+ (failed|passed|error)", l)]
    print("  ── 采集完成 ✓ 失败/错误 %d 个" % len(bad))
    for l in tail[-2:]:
        print("     %s" % l.strip()[:120])
    return bad


def main() -> int:
    mode = "--diff" if "--diff" in sys.argv else "--save"
    fast = "--fast" in sys.argv
    BASE_LOCAL = BASE if not fast else BASE.replace(".txt", "_fast.txt")
    cur = collect(fast=fast)
    if mode == "--save":
        os.makedirs(os.path.dirname(BASE_LOCAL), exist_ok=True)
        with open(BASE, "w", encoding="utf-8") as f:
            f.write("\n".join(sorted(cur)) + "\n")
        print("  ✅ 基线已保存 ✓：%s（%d 条）" % (BASE_LOCAL, len(cur)))
        return 0
    old = set()
    if os.path.exists(BASE_LOCAL):
        old = {l.strip() for l in open(BASE_LOCAL, encoding="utf-8") if l.strip()}
    else:
        print("  ⚠️ 基线不存在（先跑 --save）"); return 0
    new_bad = sorted(cur - old)
    fixed = sorted(old - cur)
    print("  ── 与基线对比 ✓（基线 %d 条）" % len(old))
    print("    **新增失败 %d 个** %s" % (len(new_bad), "✓ 无（改动安全 ✓）" if not new_bad else "✗ 需检查："))
    for x in new_bad[:20]:
        print("      + %s" % x)
    print("    已修复 %d 个" % len(fixed))
    for x in fixed[:10]:
        print("      - %s" % x)
    return 1 if new_bad else 0


if __name__ == "__main__":
    sys.exit(main())
