# -*- coding: utf-8 -*-
"""wave_state_pg_resync.py — 把磁盘上的浪型档与 PostgreSQL `wave_state` 表**对齐**（巡检 + 定向修复）。

背景（2026-10-23 事故）：`wave_state_pg_backfill.py` 于 10-21 11:49 把 90 份「**短数据窗口**」算出的
旧口径（`origin 2704.09 / wave1_top 3674.40 / pivots 3~4`）灌进了库 ✗ —— 那批文件是 10-20 在主指数
`index_daily_000001_SH.json` 还只有 ~500 根时生成的，10-21 10:21 该文件换成 2004 起的长历史后才算对。
而 `regen_wave_state_ladder.py` 当时**只重算文件、不回写库** ✗ ⇒ 读侧 `wave_store`（**PG 优先** ✓）
一直读旧锚点，磁盘文件却早已正确 —— 90 天永久不一致、且**不会自愈** ✗。

本脚本：逐份对比 `data/wave_state_history/*.json` 与库内同 `session_date` 的 payload，
**只把不一致的行按文件内容重写** ✓（幂等 ✓ `wave_pg.upsert` 自带 ON CONFLICT ✓）。
regen 补了双写后本脚本正常情况下应当输出「0 处不一致」⇒ 适合挂进巡检。

用法::
    python jobs/wave_state_pg_resync.py                 # dry-run：只列差异
    python jobs/wave_state_pg_resync.py --apply         # 按文件内容重写不一致的行
    python jobs/wave_state_pg_resync.py --sample 5      # 额外打印 5 条差异明细
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "apps", "main_line")):
    if p not in sys.path:
        sys.path.insert(0, p)

import wave_pg  # noqa: E402

# 参与比对的字段：档位/操作（决策口径）+ 锚点（铁律② 失效线，事故里出错的就是这几个）
TOP_KEYS = ("level", "sub_level", "operation", "confidence")
FEAT_KEYS = ("origin", "origin_date", "wave1_top", "wave1_top_date",
             "prior_peak", "prior_peak_date", "prev_peak", "pivots")


def _rows():
    """→ {session_date(YYYY-MM-DD): (payload, data_date)}；读库失败返回 None（fail-open ✓）。"""
    try:
        conn = wave_pg._conn()
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT session_date::text, data_date::text, payload FROM wave_state")
                return {r[0]: (r[2], r[1]) for r in cur.fetchall()}
        finally:
            conn.close()
    except Exception as e:
        print("[resync] 读库失败（%s）" % str(e)[:120], flush=True)
        return None


def _sd(ws):
    d = str(ws.get("session_date") or "").replace("-", "")[:8]
    return "%s-%s-%s" % (d[:4], d[4:6], d[6:8]) if len(d) == 8 else ""


def _fp(ws):
    """指纹：档位 + 锚点（浮点按 4 位比较，避免序列化尾差误报 ✓）。"""
    ft = ws.get("features") or {}
    out = []
    for k in TOP_KEYS:
        v = ws.get(k)
        out.append(round(v, 6) if isinstance(v, float) else v)
    for k in FEAT_KEYS:
        v = ft.get(k)
        out.append(round(v, 4) if isinstance(v, float) else v)
    return tuple(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真写库（默认 dry-run ✓）")
    ap.add_argument("--sample", type=int, default=5, help="打印几条差异明细（默认 5 ✓）")
    ap.add_argument("--dir", default=os.path.join("data", "wave_state_history"),
                    help="比对目录（默认 data/wave_state_history ✓）")
    args = ap.parse_args()

    rows = _rows()
    if rows is None:
        print("无 PG 连接（DATABASE_URL / psycopg2 不可用）⇒ 放弃")
        return 1
    files = sorted(glob.glob(os.path.join(args.dir, "*.json")))
    print("库内 %d 行 ｜ 文件 %d 份（%s）" % (len(rows), len(files), args.dir))

    diff, missing, bad = [], 0, 0
    for f in files:
        try:
            ws = json.load(open(f, encoding="utf-8")) or {}
        except Exception:
            bad += 1
            continue
        sd = _sd(ws)
        if not sd:
            bad += 1
            continue
        got = rows.get(sd)
        if got is None:
            missing += 1
            continue
        if _fp(got[0] or {}) != _fp(ws):
            diff.append((f, ws, got[0] or {}))

    print("不一致 %d ｜ 库内无此 session_date %d ｜ 跳过坏档 %d" % (len(diff), missing, bad))
    for f, ws, pgws in diff[:max(args.sample, 0)]:
        a, b = _fp(pgws), _fp(ws)
        chg = ["%s: %s→%s" % (n, x, y) for n, x, y in
               zip(TOP_KEYS + FEAT_KEYS, a, b) if x != y]
        print("  %s  %s" % (os.path.basename(f), " ｜ ".join(chg)[:200]))

    if not diff:
        print("✅ 文件与库一致（双写正常 ✓）")
        return 0
    if not args.apply:
        print("\n（dry-run；加 --apply 才写库）")
        return 0

    n_ok = 0
    for f, ws, _ in diff:
        if wave_pg.upsert(ws):
            n_ok += 1
        else:
            print("  写库失败：%s" % os.path.basename(f))
    print("写入完成：%d/%d" % (n_ok, len(diff)))
    return 0 if n_ok == len(diff) else 1


if __name__ == "__main__":
    raise SystemExit(main())
