#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 2026-09-01 重设 stock 账户账本基线（用户拍板：09-01 之前都是脏数据）。

背景
----
1) VN.PY 桥接路径成交不结算现金（见 marcus_trade._settle_bridge_cash 注释）→
   现金与成交流水长期对不上（2026-09-16 实测漂移 35,044.57）；
2) 08-28~08-31 的成交属于旧账本，且现金里混着无法追溯的历史漂移
   （例：09-02 快照有 15,599 持仓，但账本 09-01/09-02 无任何买入）。
用户决定：**从 09-01 起算，之前的当脏数据截断**。

本脚本做四件事（单事务，可 dry-run）
----
① 备份：paper_trades / paper_account_info / paper_daily_snapshot / paper_positions
   各建一张 _bak_<时间戳> 影子表（库内备份，便于回滚；不落盘不留密钥）；
② 归档：baseline 之前的成交标记 voided=1 + void_reason（统计与 FIFO 全口径即时排除）；
③ 重算：按 baseline 起的成交流水 FIFO 重放 → available_cash / 各卖出 profit，
   并同步 paper_positions 的 volume/avg_price；期初资本 initial_capital 与
   seed_initial_capital 一并设为 baseline 净值（权益曲线回放种子，避免旧基线污染）；
④ 留痕：清掉 baseline 起的脏快照（当日 15:01 任务会用新口径重写），
   并在 system_state 写一条 account_rebase_0901 记录（金额、漂移、口径）。

用法
----
    python3 jobs/rebase_account_0901.py                      # dry-run（默认，不写库）
    python3 jobs/rebase_account_0901.py --apply              # 执行
    python3 jobs/rebase_account_0901.py --archive-orderid MANUAL_xxx --apply
        # 额外归档指定 orderid（例：误写进 stock 的手工补录行）
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import psycopg2

BASELINE_DATE = "2026-09-01"
BASELINE_ASSET = 244501.79          # 09-01 期初净值（paper_daily_snapshot 08-31/09-01 两次一致，全现金零持仓）
BUY_FEE = 0.0005                    # 与 paper_engine 一致
SELL_FEE = 0.0015

BUY_WORDS = ("买入", "buy")
SELL_WORDS = ("卖出", "sell")


def db_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
        from app.config import get_settings  # type: ignore
        return get_settings().DATABASE_URL
    except Exception as e:
        raise SystemExit(f"无法获取 DATABASE_URL: {e}")


def replay(rows, baseline_asset):
    """FIFO 重放：返回 (cash, positions, realized, sells_profit, unmatched)。

    rows: [(id, trade_date, orderid, symbol, direction, price, volume, amount)]，按 id 升序。
    """
    cash = float(baseline_asset)
    lots = {}
    realized = 0.0
    sells_profit = {}
    unmatched = []
    for rid, td, oid, sym, d, px, vol, amt in rows:
        px = float(px)
        vol = int(vol)
        if d in BUY_WORDS:
            cash -= px * vol * (1 + BUY_FEE)
            lots.setdefault(sym, []).append([px, vol])
        elif d in SELL_WORDS:
            cash += px * vol * (1 - SELL_FEE)
            rem, lst, i, prof = vol, lots.get(sym, []), 0, 0.0
            while rem > 0 and i < len(lst):
                used = min(lst[i][1], rem)
                prof += (px - lst[i][0]) * used
                lst[i][1] -= used
                rem -= used
                if lst[i][1] == 0:
                    lst.pop(i)
                else:
                    i += 1
            if rem > 0:
                unmatched.append((rid, td, oid, sym, rem, px))
            realized += prof
            sells_profit[rid] = round(prof, 4)
    positions = {}
    for sym, lst in lots.items():
        tv = sum(l[1] for l in lst)
        if tv > 0:
            positions[sym] = (tv, sum(l[0] * l[1] for l in lst) / tv)
    return cash, positions, realized, sells_profit, unmatched


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写库（默认 dry-run）")
    ap.add_argument("--account", default="stock")
    ap.add_argument("--baseline-date", default=BASELINE_DATE)
    ap.add_argument("--baseline-asset", type=float, default=BASELINE_ASSET)
    ap.add_argument("--archive-orderid", action="append", default=[],
                    help="额外归档的 orderid（可重复）")
    ap.add_argument("--keep-snapshots", action="store_true",
                    help="保留 baseline 起的 paper_daily_snapshot（默认删除，交当日任务重写）")
    args = ap.parse_args()

    conn = psycopg2.connect(db_url(), connect_timeout=5)
    conn.autocommit = False
    cur = conn.cursor()

    # ── 现状 ──────────────────────────────────────────────
    cur.execute(
        "SELECT initial_capital, available_cash, seed_initial_capital "
        "FROM paper_account_info WHERE account_id = %s", (args.account,))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"paper_account_info 无 {args.account} 行")
    init_before, cash_before, seed_before = float(row[0]), float(row[1]), float(row[2] or 0)

    cur.execute(
        "SELECT id, trade_date, orderid, symbol, direction, price, volume, amount "
        "FROM paper_trades WHERE account_id = %s AND (voided = 0 OR voided IS NULL) "
        "AND COALESCE(trade_date, substr(created_at, 1, 10)) >= %s ORDER BY id",
        (args.account, args.baseline_date))
    rows = cur.fetchall()

    cur.execute(
        "SELECT count(*) FROM paper_trades WHERE account_id = %s AND (voided = 0 OR voided IS NULL) "
        "AND COALESCE(trade_date, substr(created_at, 1, 10)) < %s",
        (args.account, args.baseline_date))
    n_archive = cur.fetchone()[0]

    # 额外归档行在重放前就剔除，保证 dry-run 打印的就是 --apply 后的结果
    extra_ids = set(args.archive_orderid)
    if extra_ids:
        kept = [r for r in rows if r[2] not in extra_ids]
        n_extra = len(rows) - len(kept)
        rows = kept
        print(f"[rebase] 额外归档 orderid={sorted(extra_ids)} → 剔除 {n_extra} 行参与重放")

    # 方向词表盲区提示：FIFO/统计口径只认中文，英文行会被静默忽略
    blind = [(r[0], r[2], r[3], r[4], int(r[6])) for r in rows if r[4] in ("buy", "sell")]

    cash_after, positions, realized, sells_profit, unmatched = replay(rows, args.baseline_asset)

    print(f"[rebase] 账户={args.account} 基线={args.baseline_date} 期初净值={args.baseline_asset:,.2f}")
    print(f"[rebase] 现金: {cash_before:,.2f} → {cash_after:,.2f} (漂移 {cash_after - cash_before:+,.2f})")
    print(f"[rebase] 资本: initial {init_before:,.2f} → {args.baseline_asset:,.2f}；"
          f"seed {seed_before:,.2f} → {args.baseline_asset:,.2f}")
    print(f"[rebase] 归档 baseline 前成交 {n_archive} 行；重放 baseline 起 {len(rows)} 行")
    print(f"[rebase] 09-01 起 FIFO 已实现 = {realized:,.2f}")
    print("[rebase] 重放持仓:")
    cost_total = 0.0
    for sym, (vol, avg) in sorted(positions.items()):
        cost_total += vol * avg
        print(f"    {sym}: {vol} 股 均价 {avg:.6f} 成本 {vol * avg:,.2f}")
    print(f"    合计成本 {cost_total:,.2f}")
    if blind:
        print("[rebase] ⚠️ 英文方向行（现口径的 FIFO/统计会忽略，请确认是否该归档）:")
        for b in blind:
            print(f"    id={b[0]} orderid={b[1]} {b[2]} {b[3]} {b[4]} 股")
    if unmatched:
        print("[rebase] ⚠️ 无对应买入的卖出（重放按 0 成本计，利润记 0）:")
        for u in unmatched:
            print(f"    id={u[0]} {u[1]} {u[2]} {u[3]} 余 {u[4]} 股 @ {u[5]}")

    if not args.apply:
        print("[rebase] DRY-RUN 未写库（确认无误后加 --apply）")
        conn.rollback()
        cur.close()
        conn.close()
        return 0

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")   # 下划线：表名不能带 '-'，否则需加引号
    # ① 库内备份
    for tbl in ("paper_trades", "paper_account_info", "paper_daily_snapshot", "paper_positions"):
        cur.execute(f'CREATE TABLE IF NOT EXISTS "{tbl}_bak_{ts}" AS TABLE {tbl}')
    print(f"[rebase] 已建库内备份: *_bak_{ts}")

    # ② 归档 baseline 之前的成交
    cur.execute(
        "UPDATE paper_trades SET voided = 1, void_reason = %s, voided_at = %s "
        "WHERE account_id = %s AND (voided = 0 OR voided IS NULL) "
        "AND COALESCE(trade_date, substr(created_at, 1, 10)) < %s",
        (f"[09-01基线]期初重设：{args.baseline_date} 之前成交不参与统计与 FIFO",
         datetime.now().isoformat(), args.account, args.baseline_date))
    print(f"[rebase] 已归档 {cur.rowcount} 行")

    for oid in args.archive_orderid:
        cur.execute(
            "UPDATE paper_trades SET voided = 1, void_reason = %s, voided_at = %s "
            "WHERE account_id = %s AND orderid = %s AND (voided = 0 OR voided IS NULL)",
            ("[09-01基线]人工指定归档（误录/脏数据）", datetime.now().isoformat(),
             args.account, oid))
        print(f"[rebase] 额外归档 orderid={oid}: {cur.rowcount} 行")

    # ③ 重算：现金 / 资本 / 卖出 profit
    cur.execute(
        "UPDATE paper_account_info SET available_cash = %s, initial_capital = %s, "
        "seed_initial_capital = %s, updated_at = %s WHERE account_id = %s",
        (round(cash_after, 4), args.baseline_asset, args.baseline_asset,
         datetime.now().isoformat(), args.account))

    n_profit = 0
    for rid, prof in sells_profit.items():
        cur.execute("UPDATE paper_trades SET profit = %s WHERE id = %s AND profit IS DISTINCT FROM %s",
                    (prof, rid, prof))
        n_profit += cur.rowcount
    print(f"[rebase] 已按 baseline FIFO 重算卖出 profit: {n_profit} 行")

    # 持仓表同步（volume/avg_price 以 FIFO 为准；幽灵行删除）
    cur.execute("DELETE FROM paper_positions WHERE account_id = %s", (args.account,))
    for sym, (vol, avg) in positions.items():
        cur.execute(
            "INSERT INTO paper_positions (symbol, entry_date, highest_price, updated_at, "
            "volume, frozen, avg_price, account_id) VALUES (%s, %s, %s, %s, %s, 0, %s, %s)",
            (sym, args.baseline_date, 0, datetime.now().isoformat(), int(vol), round(avg, 6),
             args.account))
    print(f"[rebase] 已同步 paper_positions: {len(positions)} 只")

    # ④ 脏快照清理 + 留痕
    if not args.keep_snapshots:
        cur.execute("DELETE FROM paper_daily_snapshot WHERE account_id = %s AND trade_date >= %s",
                    (args.account, args.baseline_date))
        print(f"[rebase] 已删除 baseline 起的脏快照 {cur.rowcount} 行（当日 15:01 任务按新口径重写）")

    trace = {
        "baseline_date": args.baseline_date,
        "baseline_asset": args.baseline_asset,
        "cash_before": round(cash_before, 2),
        "cash_after": round(cash_after, 2),
        "drift_dropped": round(cash_after - cash_before, 2),
        "archived_rows": n_archive,
        "profit_rows_recomputed": n_profit,
        "realized_since_baseline": round(realized, 2),
        "positions": {s: {"volume": v[0], "avg_price": round(v[1], 6)} for s, v in positions.items()},
        "extra_archived_orderids": args.archive_orderid,
        "backup_suffix": ts,
        "note": "09-01 前成交与现金漂移一并截断；桥接路径现金结算已由 marcus_trade._settle_bridge_cash 修复",
    }
    cur.execute(
        "INSERT INTO system_state (key, value, updated_at) VALUES ('account_rebase_0901', %s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at",
        (json.dumps(trace, ensure_ascii=False), datetime.now()))

    conn.commit()
    cur.close()
    conn.close()
    print("[rebase] ✅ 已提交。备份表 *_bak_%s 可整表回滚。" % ts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
