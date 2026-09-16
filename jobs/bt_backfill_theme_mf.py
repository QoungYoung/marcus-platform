# -*- coding: utf-8 -*-
"""bt_backfill_theme_mf.py — **本地**补齐 `data/theme_mf_daily.json`（主题逐日主力净流入）历史。

为什么需要：`theme_mf_daily.json` 是"每个交易日一次全市场取数"的**按日缓存**，生产上只有最近几天
（实测 2026-09 的 8 个键）。全年回测钉到 1 月时，按 `≤cut` 截断 → **0 个键** → 方向层池/子方向退化
（`room_bottom` 变成主题名而不是概念、`pathA` 选不出票）→ 布腿 0 条。

生产算法（`apps/main_line/wolf_theme_vol_fund._refresh_mf_day`）：当日**个股**主力净流入（万元）
→ 按 `THEME_CONCEPTS` 的主题成分（`stock_pool.db::stock_concept_map`）加总 → 13 个主题各一个数。
本脚本同构实现，但取数用中继的 `moneyflow_dc`（东方财富口径，**含 2026 全年历史**；生产用的
`moneyflow` 接口在中继上取不到历史 → 口径差异已在文档标注）。

用法（本机）：
  python jobs/bt_backfill_theme_mf.py --start 20251201 --end 20260911 [--sleep 0.2]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import bt_env  # noqa: E402
bt_env.add_paths()


def trade_days(start: str, end: str, bars_db: str):
    c = sqlite3.connect(bars_db)
    days = [r[0] for r in c.execute(
        "SELECT DISTINCT trade_date FROM bars WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date", (start, end))]
    c.close()
    return days


def theme_members(theme_concepts, stock_pool_db):
    """主题 → 成分股代码集合（与生产同一路径：`stock_concept_map` + `THEME_CONCEPTS`）。"""
    c = sqlite3.connect(stock_pool_db)
    out = {}
    for th, cons in theme_concepts.items():
        ph = ",".join(["?"] * len(cons))
        try:
            out[th] = {str(r[0]) for r in c.execute(
                "SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name IN (%s)" % ph, cons)}
        except Exception:
            out[th] = set()
    c.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20251201")
    ap.add_argument("--end", default="20260911")
    ap.add_argument("--bars-db", default=os.path.join(bt_env.DATA, "_bt_full", "bars.sqlite"))
    ap.add_argument("--out", default=os.path.join(bt_env.DATA, "theme_mf_daily.json"))
    ap.add_argument("--sleep", type=float, default=0.15)
    a = ap.parse_args()

    import tushare_relay as tr
    from fusion_mainline import THEME_CONCEPTS

    members = theme_members(THEME_CONCEPTS, os.path.join(bt_env.DATA, "stock_pool.db"))
    print("[mf] 主题 %d 个，成分合计 %d 只" % (len(members), len(set().union(*members.values()) if members else set())),
          flush=True)

    cache = {}
    if os.path.exists(a.out):
        try:
            cache = json.load(open(a.out, encoding="utf-8")) or {}
        except Exception:
            cache = {}
    days = trade_days(a.start, a.end, a.bars_db)
    todo = [d for d in days if d not in cache]
    print("[mf] 目标 %d 天（已有缓存 %d，待取 %d）" % (len(days), len(days) - len(todo), len(todo)), flush=True)

    n_new = 0
    for i, d in enumerate(todo, 1):
        try:
            fields, items = tr.relay_items("moneyflow_dc", trade_date=d)
        except Exception as e:
            print("[mf] %s 取数失败 %s" % (d, str(e)[:80]), flush=True)
            continue
        if not items:
            print("[mf] %s 无数据（跳过）" % d, flush=True)
            continue
        try:
            i_ts = fields.index("ts_code") if "ts_code" in fields else 0
            i_net = fields.index("net_amount") if "net_amount" in fields else len(fields) - 1
        except Exception:
            i_ts, i_net = 0, len(fields) - 1
        by_ts = {}
        for it in items:
            try:
                by_ts[str(it[i_ts])] = float(it[i_net] or 0)
            except (TypeError, ValueError, IndexError):
                continue
        row = {}
        for th, codes in members.items():
            row[th] = round(sum(by_ts.get(c, 0.0) for c in codes), 2)
        cache[d] = row
        n_new += 1
        if i % 10 == 0 or i == len(todo):
            json.dump(cache, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
            print("[mf] 进度 %d/%d（最新 %s）" % (i, len(todo), d), flush=True)
        time.sleep(a.sleep)

    json.dump(cache, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
    ks = sorted(cache)
    print("[mf] 完成：本次新增 %d 天，缓存共 %d 天（%s → %s）→ %s"
          % (n_new, len(ks), ks[0] if ks else "-", ks[-1] if ks else "-", a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
