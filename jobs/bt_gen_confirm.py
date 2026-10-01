# -*- coding: utf-8 -*-
"""bt_gen_confirm.py — **布腿前现算当日候选**（账本 §9.408 ✓ 用户「要让回测用上改动」✓）

为什么独立成进程 ✓：在 `bt_prod_run` 进程内调用判官时，钉钟 shim 的 sqlite 报
  `unable to open database file` ✗（本机单独跑判官/单独装 shim 都正常 ✓ ⇒ 属进程内状态问题 ✗）
  ⇒ 用**干净子进程**做同一件事：自己装 shim（同样的 `--bars-db` ✓）＋ 把 DATA 指向本臂沙箱
    ⇒ 判官读到的是**当天 as-of 数据** ✓，写出的是**本臂沙箱**的 `stock_confirm_result.json` ✓

用法 ✓：python jobs/bt_gen_confirm.py --day 20260305 --root data/_bt_t35 --bars-db <sqlite>
生产零影响 ✓：只被 `bt_prod_run` 在 `WOLF_BT_GEN_CONFIRM=1` 时调用 ✓
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--root", default=os.path.join(REPO, "data", "_bt_t35"))
    ap.add_argument("--bars-db", default=os.path.join(REPO, "data", "_bt_full", "bars.sqlite"))
    a = ap.parse_args()
    day = str(a.day).replace("-", "")
    sandbox = a.root if os.path.isabs(a.root) else os.path.join(REPO, a.root)
    day_dir = os.path.join(sandbox, day)
    if not os.path.isdir(day_dir):
        os.makedirs(day_dir, exist_ok=True)
    os.chdir(REPO)
    sys.path.insert(0, os.path.join(REPO, "jobs"))
    sys.path.insert(0, os.path.join(REPO, "backend"))
    sys.path.insert(0, os.path.join(REPO, "apps", "main_line"))
    try:
        import bt_run_pinned as brp
        cut = day                              # ⚠️ shim 要 YYYYMMDD（带横线会 int("3-") 崩 ✗）
        brp.install_relay_shim(a.bars_db, cut)
        try:
            brp.install_gzcloud_shim(a.bars_db, cut)
        except Exception:
            pass
        import stock_confirm_judge as SCJ
        SCJ.DATA = day_dir                     # ← 写到**本臂沙箱这一天** ✓
        SCJ.DB = os.path.join(day_dir, "stock_pool.db")
        os.environ["STOCK_CONFIRM_ORDER"] = os.getenv("STOCK_CONFIRM_ORDER", "")
        SCJ.main()
        print("[gen_confirm] ✅ 已现算 %s 的候选 ✓（order=%s ✓｜DATA=%s ✓）"
              % (day, os.getenv("STOCK_CONFIRM_ORDER"), day_dir), flush=True)
        return 0
    except Exception as e:
        print("[gen_confirm] ❌ 现算失败（调用方 fail-open ✓）: %s\n%s"
              % (str(e)[:200], traceback.format_exc()[-1200:]), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
