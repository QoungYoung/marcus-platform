# -*- coding: utf-8 -*-
"""把**已存在的**浪型档全部回填进 PostgreSQL 的 wave_state 表（步 2 ✓）。

数据源（只读 ✓ 不改任何文件）：
  ① `data/wave_state.json`（live）
  ② `data/wave_state_<YYYY-MM-DD>.json`（按数据日命名）
  ③ `data/wave_state_session_<YYYY-MM-DD>.json`（按服务日命名 ✓ 2026-10-21 起）
  ④ `data/wave_state_history/<YYYYMMDD>.json`（回测取档 ✓）
  ⑤ `data/_bt_*/**/wave_state*.json`（可选 `--include-bt` ✗ 默认不碰 ✓）

去重口径：同一 `session_date` 只留**最新的一份** ✓ —— 先比 `data_date` ✓，再比文件 mtime ✓
（避免旧回填件把新档覆盖 ✗）。

用法::
    python jobs/wave_state_pg_backfill.py                 # dry-run：只统计会写多少行
    python jobs/wave_state_pg_backfill.py --apply         # 真写（幂等 ✓ 可重复跑 ✓）
    python jobs/wave_state_pg_backfill.py --apply --include-bt   # 连回测沙箱里的档一起灌
"""
import argparse
import glob
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "apps", "main_line")):
    if p not in sys.path:
        sys.path.insert(0, p)

import wave_pg  # noqa: E402


def _files(include_bt):
    pats = [
        os.path.join("data", "wave_state.json"),
        os.path.join("data", "wave_state_[0-9]*.json"),
        os.path.join("data", "wave_state_session_*.json"),
        os.path.join("data", "wave_state_history", "*.json"),
    ]
    if include_bt:
        pats.append(os.path.join("data", "_bt_*", "**", "wave_state*.json"))
    out = []
    for pat in pats:
        out.extend(glob.glob(pat, recursive=include_bt))
    return sorted(set(out))


def _date8(s):
    d = str(s or "").replace("-", "")[:8]
    return d if len(d) == 8 and d.isdigit() else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真写库（默认 dry-run ✓）")
    ap.add_argument("--include-bt", action="store_true", help="连 _bt_* 回测沙箱的档一起灌")
    ap.add_argument("--allow-legacy", action="store_true",
                    help="连**老 agent**（无 session_date）时代的档也灌（默认拒收 ✗）")
    args = ap.parse_args()

    files = _files(args.include_bt)
    print("候选文件：%d 个（include_bt=%s）" % (len(files), args.include_bt))

    best = {}       # session_date -> (data_date, mtime, path, ws)
    bad = 0
    legacy = 0
    for f in files:
        try:
            ws = json.load(open(f, encoding="utf-8"))
        except Exception:
            bad += 1
            continue
        if not isinstance(ws, dict):
            bad += 1
            continue
        sd = _date8(ws.get("session_date"))
        dd = _date8(ws.get("date")) or sd
        if not sd:
            # ★ **拒收老 agent 时代的档** ✗（它们没有 session_date/family ⇒ 语义与浪型链不同 ✗，
            #   灌进去会让"这行是谁算的"说不清 ✓；如确需，用 --allow-legacy ✓）
            if not args.allow_legacy:
                legacy += 1
                continue
            if not dd:
                bad += 1
                continue
            try:
                import datetime as _dt
                d = _dt.date(int(dd[:4]), int(dd[4:6]), int(dd[6:8]))
                for _ in range(10):
                    d += _dt.timedelta(days=1)
                    if d.weekday() < 5:
                        break
                sd = d.strftime("%Y%m%d")
            except Exception:
                bad += 1
                continue
            ws = dict(ws)
            ws["session_date"] = sd
        mtime = os.path.getmtime(f)
        key = (dd, mtime)
        cur = best.get(sd)
        if cur is None or key > (cur[0], cur[1]):
            best[sd] = (dd, mtime, f, ws)

    print("可用档：%d 天（坏档 %d ｜ 拒收老 agent 档 %d）｜ session_date 范围 %s ~ %s" % (
        len(best), bad, legacy, min(best) if best else "-", max(best) if best else "-"))
    dist = {}
    for sd, (dd, _m, _f, ws) in best.items():
        dist[ws.get("operation") or "?"] = dist.get(ws.get("operation") or "?", 0) + 1
    print("operation 分布：", dict(sorted(dist.items(), key=lambda kv: -kv[1])))

    if not args.apply:
        print("\n（dry-run ✓ 未写库；加 --apply 才写）")
        return 0

    rows = [best[k][3] for k in sorted(best)]
    t0 = time.time()
    n = wave_pg.upsert_many(rows)
    st = wave_pg.stats()
    print("写库完成：成功 %d / %d 行，耗时 %.1fs" % (n, len(rows), time.time() - t0))
    print("表状态：", st)
    return 0 if n == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
