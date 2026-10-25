#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""break_stop_run.py — 破位止损卡（P4 编排入口）。

用法：
    python jobs/break_stop_run.py --mode alert --live
    python jobs/break_stop_run.py --mode alert --asof 20261023 --no-qq --force
    python jobs/break_stop_run.py --mode off

说明：
  · `--mode alert`（默认）= 真实计算 + 写 break_events/break_actions + 推 QQ，**不下单**
  · `--mode execute_index` = 预留；P5 回测通过 + 用户批准前不执行
  · `WOLF_BREAK_STOP=0`（默认）时脚本直接跳过；`--force` 可手动跑
  · `--live` 用腾讯实时 quote 追加当日 bar；历史回放不要加
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", default="alert", choices=("alert", "off", "execute_index"))
    p.add_argument("--asof", default=None)
    p.add_argument("--live", action="store_true")
    p.add_argument("--no-qq", action="store_true")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    from app.services.break_stop_service import BreakStopService, enabled
    if not args.force and not enabled():
        print(json.dumps({"ok": False, "skipped": "WOLF_BREAK_STOP=0"}, ensure_ascii=False))
        return 0
    if args.mode == "execute_index":
        print(json.dumps({"ok": False, "error": "execute_index disabled until P5"}, ensure_ascii=False))
        return 2
    try:
        out = BreakStopService().run(mode_=args.mode, asof=args.asof,
                                     live_quote=bool(args.live),
                                     push_qq=not args.no_qq)
        print(json.dumps(out, ensure_ascii=False, default=str))
        return 0 if out.get("ok") else 1
    except Exception as e:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:300])},
                         ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
