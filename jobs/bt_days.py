# -*- coding: utf-8 -*-
"""bt_days.py — 全拟真回测：**多日流水线**（按日 seed → 两条布腿路径 → 汇总）。

每个交易日 T 依次做：
  1. `bt_seed_day.py --date T`      建 as-of 沙箱（含该日代码版本树、时钟打桩、gzcloud/relay 替身）
  2. `bt_day_legs_switch.py --date T`  08:18 路径（switch_builder）——**注意它用的是上一交易日的确认域**
  3. `bt_day_legs.py --date T`       09:20 路径（rotation_switch_arm）
  4. 汇总 `<out>/legs_all.jsonl` + `<out>/legs_by_day.json`

用法（容器内）：
  python jobs/bt_days.py --start 20260909 --end 20260915 [--skip-seed] [--out /app/data/_bt_full/_summary]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

DATA = os.environ.get("DATA_DIR", "/app/data")
ROOT = os.path.join(DATA, "_bt_full")


def trade_days(start: str, end: str, bars_db: str):
    import sqlite3
    c = sqlite3.connect(bars_db)
    days = [r[0] for r in c.execute(
        "SELECT DISTINCT trade_date FROM bars WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date", (start, end))]
    c.close()
    return days


def prev_trade_day(d8: str, bars_db: str) -> str:
    import sqlite3
    c = sqlite3.connect(bars_db)
    r = c.execute("SELECT max(trade_date) FROM bars WHERE trade_date < ?", (d8,)).fetchone()
    c.close()
    return r[0] if r and r[0] else d8


def run(cmd, log_path, timeout=3600):
    t0 = time.time()
    with open(log_path, "w") as f:
        rc = subprocess.call(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
    return rc, time.time() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--bars-db", default=os.path.join(ROOT, "bars.sqlite"))
    ap.add_argument("--out", default=os.path.join(ROOT, "_summary"))
    ap.add_argument("--skip-seed", action="store_true", help="沙箱已建好时跳过 seed")
    ap.add_argument("--llm-mode", default=os.getenv("BT_LLM_MODE", "record"), choices=["record", "replay"])
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    days = trade_days(a.start, a.end, a.bars_db)
    print("[days] %d 个交易日：%s → %s" % (len(days), days[0] if days else "-", days[-1] if days else "-"), flush=True)
    by_day, all_legs = {}, []
    for d8 in days:
        sb = os.path.join(ROOT, d8)
        cut = prev_trade_day(d8, a.bars_db)
        entry = {"date": d8, "cut": cut, "steps": {}}
        # ① seed
        if not a.skip_seed:
            rc, dt = run([sys.executable, "/app/jobs/bt_seed_day.py", "--date", d8],
                         os.path.join(a.out, "seed_%s.log" % d8), timeout=3600)
            entry["steps"]["seed"] = {"rc": rc, "s": round(dt, 1)}
            print("[days] %s seed rc=%d %.0fs" % (d8, rc, dt), flush=True)
        # ② 08:18 路径：用**上一交易日**的确认域（switch_builder 08:18 跑，confirm 08:20 才刷新）
        prev = prev_trade_day(cut, a.bars_db)
        rc_prev, dtp = run([sys.executable, "/app/jobs/bt_run_pinned.py", "--as-of", prev,
                            "--data-dir", sb, "--bars-db", a.bars_db,
                            "--code-dir", os.path.join(DATA, "_bt_code", "rev_" + (
                                (json.load(open(os.path.join(DATA, "_bt_code", "rev_map.json"), encoding="utf-8"))
                                 .get(d8, {}) or {}).get("rev", ""))),
                            "--script", _script_in_rev(d8, "apps/main_line/stock_confirm_judge.py")],
                           os.path.join(a.out, "confirm_%s.log" % d8), timeout=1800)
        entry["steps"]["confirm_prevday"] = {"rc": rc_prev, "as_of": prev, "s": round(dtp, 1)}
        rc_sw, dts = run([sys.executable, "/app/jobs/bt_day_legs_switch.py", "--date", d8, "--held-from-db"],
                         os.path.join(a.out, "switch_%s.log" % d8), timeout=1800)
        entry["steps"]["switch_0818"] = {"rc": rc_sw, "s": round(dts, 1)}
        # ③ 09:20 路径
        rc_arm, dta = run([sys.executable, "/app/jobs/bt_day_legs.py", "--date", d8, "--held-from-db"],
                          os.path.join(a.out, "arm_%s.log" % d8), timeout=2400)
        entry["steps"]["arm_0920"] = {"rc": rc_arm, "s": round(dta, 1)}
        # ④ 汇总当日腿
        legs = []
        for fn, src in (("legs_switch.jsonl", "switch_0818"), ("legs.jsonl", "arm_0920")):
            p = os.path.join(sb, fn)
            if os.path.exists(p):
                for ln in open(p, encoding="utf-8"):
                    ln = ln.strip()
                    if ln:
                        try:
                            legs.append(json.loads(ln))
                        except Exception:
                            pass
        entry["n_legs"] = len(legs)
        entry["legs"] = [l.get("symbol") for l in legs]
        by_day[d8] = entry
        all_legs.extend(legs)
        print("[days] %s cut=%s → 腿 %d 条 %s" % (d8, cut, len(legs), entry["legs"]), flush=True)

    with open(os.path.join(a.out, "legs_all.jsonl"), "w", encoding="utf-8") as f:
        for l in all_legs:
            f.write(json.dumps(l, ensure_ascii=False) + "\n")
    with open(os.path.join(a.out, "legs_by_day.json"), "w", encoding="utf-8") as f:
        json.dump(by_day, f, ensure_ascii=False, indent=1)
    print("[days] 完成：%d 天 / %d 条腿 → %s" % (len(by_day), len(all_legs), a.out), flush=True)
    return 0


def _script_in_rev(d8: str, rel: str) -> str:
    """该日版本树里的脚本路径（不存在则回现行 /app 下的）。"""
    try:
        m = json.load(open(os.path.join(DATA, "_bt_code", "rev_map.json"), encoding="utf-8"))
        rev = ((m.get(d8) or {}).get("rev")) or ""
        p = os.path.join(DATA, "_bt_code", "rev_%s" % rev, rel)
        if rev and os.path.exists(p):
            return p
    except Exception:
        pass
    return os.path.join("/app", rel)


if __name__ == "__main__":
    sys.exit(main())
