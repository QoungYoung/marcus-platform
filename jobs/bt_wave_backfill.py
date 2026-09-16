# -*- coding: utf-8 -*-
"""bt_wave_backfill.py — 给已有沙箱补跑「波浪判定 agent」（LLM 录制/回放）。

为什么需要：`bt_seed_day.py` 的 ③ 步会调 `bt_wave_asof.py` 把 `wave_state.json` 写进沙箱；
本地 LLM 隧道断掉时这一步会失败 → 沙箱里只剩**空占位**（无 `operation`）→
`daily_decision` 的 L2/L5 拿不到波浪 → **L5 默认放行**，年跑会系统性偏乐观。
（实测：73 个沙箱 wave_state.json 全部是空占位。）

本脚本对每个沙箱按 seed 的同一条调用补跑：
    bt_wave_asof.py --as-of <cut> --mode record --main-line-state <sb>/main_line_state.json
                    --out <sb>/wave_state.json        env: DATA_DIR=<sb>, WAVE_CHAT_URL=<本地隧道>
跑完打印逐日结果（ok / llm_err / 耗时），并把汇总写 `<root>/_summary/wave_backfill.json`。

用法：
  WAVE_CHAT_URL=http://127.0.0.1:13001/chat python jobs/bt_wave_backfill.py \
      --root data/_bt_year [--force] [--limit N] [--only 20260105,20260106]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

sys.path[:0] = []
import bt_env  # noqa: E402


def wave_ok(path: str) -> bool:
    try:
        w = json.load(open(path, encoding="utf-8"))
    except Exception:
        return False
    return bool(w.get("operation"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(bt_env.DATA, "_bt_year"))
    ap.add_argument("--mode", default=os.getenv("BT_LLM_MODE", "record"), choices=["record", "replay"])
    ap.add_argument("--force", action="store_true", help="已有有效 wave 也重跑")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="", help="只跑这些天（逗号分隔）")
    ap.add_argument("--bars-db", default=os.path.join(bt_env.DATA, "_bt_full", "bars.sqlite"))
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()

    only = {x.strip() for x in a.only.split(",") if x.strip()}
    days = []
    for name in sorted(os.listdir(a.root)):
        sb = os.path.join(a.root, name)
        if not (name.isdigit() and len(name) == 8 and os.path.isdir(sb)):
            continue
        if only and name not in only:
            continue
        if not os.path.exists(os.path.join(sb, "_seed.json")):
            continue
        if not a.force and wave_ok(os.path.join(sb, "wave_state.json")):
            continue
        days.append((name, sb))
    if a.limit:
        days = days[:a.limit]
    print("[wavebf] 待补 %d 个沙箱 → %s" % (len(days), a.root), flush=True)

    chat = os.getenv("WAVE_CHAT_URL", "")
    if not chat:
        print("[wavebf] ⚠️ 未设 WAVE_CHAT_URL（应为本地隧道 http://127.0.0.1:13001/chat）", flush=True)
    res = {}
    n_ok = n_err = 0
    for i, (day, sb) in enumerate(days, 1):
        try:
            cut = str((json.load(open(os.path.join(sb, "_seed.json"), encoding="utf-8")) or {}).get("cut") or "")
        except Exception:
            cut = ""
        if not cut:
            print("[wavebf] %s 无 cut，跳过" % day, flush=True)
            res[day] = {"status": "no_cut"}
            continue
        t0 = time.time()
        try:
            # ⚠️ 必须经 `bt_run_pinned`（钉钟 + relay/gzcloud 替身 + 本地 pro.* 替身 + 断网），
            #    与 bt_seed_day 的波浪步同一条路径；直接调 bt_wave_asof 会走真实中继 → 本机无代理失败。
            r = subprocess.run(
                [sys.executable, bt_env.jobs_file("bt_run_pinned.py"), "--as-of", cut,
                 "--data-dir", sb, "--bars-db", a.bars_db,
                 "--script", bt_env.jobs_file("bt_wave_asof.py"), "--",
                 "--as-of", cut, "--mode", a.mode,
                 "--main-line-state", os.path.join(sb, "main_line_state.json"),
                 "--out", os.path.join(sb, "wave_state.json")],
                capture_output=True, text=True, timeout=a.timeout,
                env={**os.environ, "DATA_DIR": sb,
                     "BT_LLM_CACHE": os.environ.get("BT_LLM_CACHE", os.path.join(bt_env.DATA, "_bt_llm"))})
            ok = wave_ok(os.path.join(sb, "wave_state.json"))
            st = "ok" if ok else ("rc%d" % r.returncode)
            tail = (r.stdout or r.stderr or "")[-120:].replace("\n", " ")
        except Exception as e:
            st, tail = "exc", str(e)[:120]
            r = None
        dt = round(time.time() - t0, 1)
        res[day] = {"status": st, "cut": cut, "s": dt, "tail": tail}
        if st == "ok":
            n_ok += 1
        else:
            n_err += 1
        print("[wavebf] %d/%d %s cut=%s → %s (%.1fs) %s" % (i, len(days), day, cut, st, dt, "" if st == "ok" else tail),
              flush=True)

    out = os.path.join(a.root, "_summary", "wave_backfill.json")
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass
    print("[wavebf] 完成：ok=%d err=%d → %s" % (n_ok, n_err, out), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
