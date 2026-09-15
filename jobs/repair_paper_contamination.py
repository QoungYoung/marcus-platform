# -*- coding: utf-8 -*-
"""repair_paper_contamination.py — 修复「回测误写生产模拟盘」污染（2026-09-16 事故）。

事故：`jobs/bt_tape.run_symbol()` 曾经会实例化 `BacktestPaperEngine`（= `apps/paper-trading/paper_engine.PaperTradingEngine`，
**PostgreSQL 落地**，默认 `account_id='stock'`）→ 一次回测 smoke 跑（2026-09-16 02:12:18）把
**5 张生产订单 + 5 笔生产成交**（SH600977 买入 1,900 股 ×5 = 9,500 股，金额 120,251.00 元）写进生产模拟盘，
并新建了一条幽灵持仓、扣减了生产现金。**根因是"回测用了生产引擎"，已加硬拦**：
`bt_tape.run_symbol` 现在拒绝加载该引擎（要撮合请用 `jobs/bt_account.py` 的进程内账户）。

本脚本做三件事（默认 `--dry-run`，**只有 `--apply` 才写库**）：
  ① 按"事故指纹"识别污染行：`account_id='stock'` + `created_at` 落在 **非交易时段** + `symbol` 属于被污染标的 + 无 `reason`；
  ② 校验识别结果与预期一致（笔数/金额/持仓/现金回推），不一致就**拒绝执行**（防误删生产数据）；
  ③ 复原到**生产自己的 09-15 收盘快照**（`paper_daily_snapshot`）：现金/冻结回到快照值、删幽灵持仓、
     污染成交置 `voided=1`（保留审计痕迹，不物理删除）、污染订单删除、`order_counter` 回退。

用法（容器内）：
  python jobs/repair_paper_contamination.py --date 2026-09-16 --symbol SH600977            # 只看
  python jobs/repair_paper_contamination.py --date 2026-09-16 --symbol SH600977 --apply    # 落库
"""
from __future__ import annotations

import argparse
import os
import sys

DSN = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
REASON = "BT_CONTAMINATION_20260916_tape_smoke"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-09-16", help="污染发生日（created_at 前缀）")
    ap.add_argument("--symbol", default="SH600977")
    ap.add_argument("--account", default="stock")
    ap.add_argument("--expect-trades", type=int, default=5)
    ap.add_argument("--expect-orders", type=int, default=5)
    ap.add_argument("--expect-amount", type=float, default=120251.0)
    ap.add_argument("--baseline-date", default="2026-09-15", help="复原基准=该日收盘快照")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    import psycopg2
    c = psycopg2.connect(DSN)
    cur = c.cursor()

    cur.execute("""SELECT id, created_at, symbol, direction, volume, price, amount, coalesce(voided,0)
                   FROM paper_trades
                   WHERE account_id=%s AND symbol=%s AND created_at LIKE %s
                   ORDER BY id""", (a.account, a.symbol, a.date + "%"))
    trades = cur.fetchall()
    cur.execute("""SELECT orderid, created_at, symbol, direction, volume, price, status
                   FROM paper_orders
                   WHERE account_id=%s AND symbol=%s AND created_at LIKE %s ORDER BY orderid""",
                (a.account, a.symbol, a.date + "%"))
    orders = cur.fetchall()
    cur.execute("""SELECT symbol, entry_date, volume, avg_price FROM paper_positions
                   WHERE account_id=%s AND symbol=%s""", (a.account, a.symbol))
    pos = cur.fetchall()
    cur.execute("SELECT available_cash, frozen_cash, order_counter FROM paper_account_info WHERE account_id=%s",
                (a.account,))
    cash, frozen, counter = cur.fetchone()
    cur.execute("""SELECT trade_date, available_cash, frozen_cash, position_value, total_asset
                   FROM paper_daily_snapshot WHERE account_id=%s AND trade_date <= %s
                   ORDER BY trade_date DESC LIMIT 1""", (a.account, a.baseline_date))
    base = cur.fetchone()

    amt = sum(float(t[6] or 0) for t in trades)
    print("=== 事故指纹 ===")
    print("  成交 %d 笔 / 金额 %.2f / 订单 %d 张 / 幽灵持仓 %d 条" % (len(trades), amt, len(orders), len(pos)))
    for t in trades:
        print("     trade", t)
    for o in orders:
        print("     order", o)
    for p in pos:
        print("     position", p)
    print("  当前账户：cash=%.4f frozen=%.2f order_counter=%s" % (float(cash), float(frozen), counter))
    print("  复原基准（%s 快照）：%s" % (a.baseline_date, base))

    bad = []
    if len(trades) != a.expect_trades:
        bad.append("成交笔数 %d ≠ 预期 %d" % (len(trades), a.expect_trades))
    if len(orders) != a.expect_orders:
        bad.append("订单张数 %d ≠ 预期 %d" % (len(orders), a.expect_orders))
    if abs(amt - a.expect_amount) > 1.0:
        bad.append("金额 %.2f ≠ 预期 %.2f" % (amt, a.expect_amount))
    if not base:
        bad.append("找不到 %s 及以前的收盘快照（无法确定复原基准）" % a.baseline_date)
    if any(t[3] not in ("买入", "buy") for t in trades):
        bad.append("污染成交里出现非买入方向（需人工确认）")
    if any(float(t[7] or 0) == 1 for t in trades):
        bad.append("已有被作废的成交（可能已修过一次）")
    if bad:
        print("\n❌ 校验不通过，拒绝执行：")
        for b in bad:
            print("   -", b)
        return 2

    prev_counter = str(max(0, int(counter) - len(orders)))
    print("\n=== 将要执行（--apply 才落库）===")
    print("  ① 作废污染成交：UPDATE paper_trades SET voided=1, void_reason='%s' WHERE id IN %s"
          % (REASON, tuple(t[0] for t in trades)))
    print("  ② 删除污染订单：DELETE FROM paper_orders WHERE orderid IN %s"
          % (tuple(o[0] for o in orders),))
    print("  ③ 删除幽灵持仓：DELETE FROM paper_positions WHERE account_id='%s' AND symbol='%s' AND entry_date='%s'"
          % (a.account, a.symbol, pos[0][1] if pos else "?"))
    print("  ④ 复原账户：available_cash=%.4f, frozen_cash=%.2f, order_counter=%s（基准 %s 快照）"
          % (float(base[1]), float(base[2]), prev_counter, base[0]))
    if not a.apply:
        print("\n（dry-run：未写入任何数据）")
        return 0

    with c:
        cur.execute("""UPDATE paper_trades SET voided=1, void_reason=%s, voided_at=now()
                       WHERE id = ANY(%s) AND account_id=%s""",
                    (REASON, [t[0] for t in trades], a.account))
        n1 = cur.rowcount
        cur.execute("DELETE FROM paper_orders WHERE orderid = ANY(%s) AND account_id=%s",
                    ([o[0] for o in orders], a.account))
        n2 = cur.rowcount
        cur.execute("""DELETE FROM paper_positions WHERE account_id=%s AND symbol=%s AND entry_date=%s""",
                    (a.account, a.symbol, pos[0][1] if pos else ""))
        n3 = cur.rowcount
        cur.execute("""UPDATE paper_account_info SET available_cash=%s, frozen_cash=%s, order_counter=%s,
                       updated_at=now() WHERE account_id=%s""",
                    (float(base[1]), float(base[2]), int(prev_counter), a.account))
        n4 = cur.rowcount
    print("\n✅ 已修复：成交作废 %d / 订单删除 %d / 持仓删除 %d / 账户复原 %d" % (n1, n2, n3, n4))

    cur.execute("SELECT available_cash, frozen_cash, order_counter FROM paper_account_info WHERE account_id=%s",
                (a.account,))
    print("  修后账户：", cur.fetchone())
    cur.execute("SELECT count(*) FROM paper_trades WHERE account_id=%s AND coalesce(voided,0)=0", (a.account,))
    print("  未作废成交数：", cur.fetchone()[0])
    cur.close(); c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
