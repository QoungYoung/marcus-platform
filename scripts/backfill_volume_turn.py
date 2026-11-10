#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""backfill_volume_turn.py — 用 Tushare 历史分钟线回放「缩转放」监控。

为什么需要：
    缩转放监控 2026-11-06 才上线，11-04 那天它没有运行记录（scheduler 日志 0 次），
    但 datahubco/promax 的 stk_mins 有历史 1 分钟线 ⇒ 可以按**同一套判据**回放，
    写进 volume_turn_intraday（source='backfill'），用于验收/回放。

判据与 jobs/wolf_volume_turn_alert.py 完全一致：
    ① 分时相对放量：最新 15 分钟桶 ÷ 此前中位桶 ≥ 1.3，且 ≥ 前一桶 ×1.1（要有拐点）
    ② 价格配合：现价 > 当日 VWAP，且近 15 分钟上行
    ③ 整体缩量：今日全天额 ÷ 昨日额 < 1.0 —— **只作标签**，不作硬门
       （11-04 是放量日，① ② 成立、③ 不成立；监控上线时曾把 ③ 当硬门 ⇒ 会漏掉 11-04）

用法：
    python scripts/backfill_volume_turn.py --date 20261104 --code 588170.SH
    python scripts/backfill_volume_turn.py --date 20261104 --code 588170.SH --dry
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "backend")):
    if p not in sys.path:
        sys.path.insert(0, p)

BUCKET = 15
TURN_MIN = 1.3
DAY_SHRINK_MAX = 1.0


def _day_ratio(date8: str):
    """指数今日额 ÷ 昨日额（只作 shrink 标签）；失败返回 (None, None, None)。"""
    try:
        import pandas as pd
        from core.tushare_relay import relay_query
        d = relay_query("index_daily", ts_code="000001.SH",
                        start_date="20261001", end_date=date8)
        a = pd.to_numeric(d.sort_values("trade_date").amount, errors="coerce") / 1e5
        if len(a) < 2:
            return None, None, None
        prev, today = float(a.iloc[-2]), float(a.iloc[-1])
        return (round(today / prev, 2) if prev else None), round(prev), round(today)
    except Exception as e:
        print("[warn] 日额比失败: %s" % str(e)[:80], file=sys.stderr)
        return None, None, None


def _rows(date8: str, code: str):
    from core.tushare_relay import relay_query
    d = relay_query("stk_mins", ts_code=code, freq="1min",
                    start_date="%s 09:30:00" % date8, end_date="%s 15:00:00" % date8)
    d = d.sort_values("trade_time")
    out = []
    for _, r in d.iterrows():
        hm = str(r["trade_time"])[11:16]
        if hm == "09:30":          # 与腾讯口径一致：dv 从 09:31 开始，集合竞价量不计入分桶
            continue
        out.append((hm, float(r["close"]), float(r["vol"] or 0)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--code", default="588170.SH")
    ap.add_argument("--step", type=int, default=3, help="每隔几分钟落一行")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    date8 = a.date.replace("-", "")[:8]

    rows = _rows(date8, a.code)
    if len(rows) < 50:
        print("[skip] 分钟线不足: %d" % len(rows))
        return 0
    px = [r[1] for r in rows]
    dv = [r[2] for r in rows]
    dy, prev_yi, today_yi = _day_ratio(date8)
    shrink = (dy is not None and dy < DAY_SHRINK_MAX)

    recs = []
    for i in range(BUCKET * 3 - 1, len(rows), max(1, a.step)):
        n = (i + 1) // BUCKET
        if n < 3:
            continue
        buckets = [sum(dv[k * BUCKET:(k + 1) * BUCKET]) for k in range(n)]
        hist = buckets[:-1] or buckets
        base = sorted(hist)[len(hist) // 2]
        ratio = (buckets[-1] / base) if base > 0 else 0.0
        lvp = (buckets[-1] / buckets[-2]) if len(buckets) >= 2 and buckets[-2] > 0 else 0.0
        vwap = sum(px[k] * dv[k] for k in range(i + 1)) / (sum(dv[:i + 1]) or 1.0)
        k15 = min(BUCKET, i)
        last15 = (px[i] / px[i - k15] - 1) * 100 if i - k15 >= 0 and px[i - k15] else 0.0
        turn = (ratio >= TURN_MIN) and (lvp >= 1.1)
        price_ok = (px[i] > vwap) and (last15 > 0)
        recs.append((date8, str(rows[i][0]).replace(":", "")[:4], round(ratio, 2), round(last15, 2), dy,
                     None, prev_yi, bool(shrink), bool(turn), bool(price_ok), "backfill"))

    fired = [r for r in recs if r[8] and r[9]]
    print(f"{date8} {a.code}: bars={len(rows)} recs={len(recs)} "
          f"turn+price_ok 行数={len(fired)} 首个={fired[0][1] if fired else '-'} "
          f"末个={fired[-1][1] if fired else '-'}（dy={dy} shrink={shrink}）")
    if a.dry:
        for r in fired[:8]:
            print("  ", r)
        return 0

    import psycopg2
    from core import dsn
    with psycopg2.connect(dsn.main(), connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("""alter table volume_turn_intraday
                           add column if not exists source varchar(16) default 'live'""")
            for r in recs:
                cur.execute("""insert into volume_turn_intraday(
                    session_date,as_of_time,bucket_ratio,last15_pct,dy_ratio,est_yi,prev_yi,
                    shrink,turn,price_ok,source) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    on conflict (session_date,as_of_time) do update set
                    bucket_ratio=excluded.bucket_ratio, last15_pct=excluded.last15_pct,
                    dy_ratio=excluded.dy_ratio, prev_yi=excluded.prev_yi,
                    shrink=excluded.shrink, turn=excluded.turn, price_ok=excluded.price_ok,
                    source=excluded.source, updated_at=now()""", r)
        conn.commit()
    print(f"[ok] 写入 {len(recs)} 行（source=backfill）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
