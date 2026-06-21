# -*- coding: utf-8 -*-
"""
标记 T+0 违规的历史交易
============================
问题: 旧版回测引擎在 place_sandbox_order 没调 set_current_date,
     导致 _is_t1_locked() 永远返回 False, A 股 T+1 形同虚设.

本脚本:
  1. 对 backtest_trades 表中同日 (symbol, trade_date) 同时有 buy+sell 的记录打标
  2. 标记列: is_t0_violation = TRUE, t0_violation_note = 详细说明
  3. 不修改 profit / 持仓数据, 只标注诊断信息

注: 已在 backtest.py:1786 修复 set_current_date 缺失问题 (新回测不会重现)
"""
import sys, io
from collections import defaultdict
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from sqlalchemy import asc
from app.database import SessionLocal, init_db
from app.models.backtest_orm import BacktestTrade


def main():
    print("=" * 70)
    print("[Mark T+0] 1. 应用 schema patches ...")
    init_db()

    db = SessionLocal()
    try:
        all_trades = db.query(BacktestTrade).order_by(
            asc(BacktestTrade.task_id), asc(BacktestTrade.symbol),
            asc(BacktestTrade.trade_date), asc(BacktestTrade.id)
        ).all()
        print(f"[Mark T+0] 2. 总交易: {len(all_trades)} 笔")

        # 按 (task_id, symbol, date) 分组
        groups = defaultdict(list)
        for t in all_trades:
            groups[(t.task_id, t.symbol, t.trade_date)].append(t)

        # 找同日 buy+sell 同时存在的
        violations_by_trade = {}  # trade_id -> note
        total_violation_groups = 0
        for (tid, sym, td), trades in groups.items():
            has_buy = any(t.direction == "buy" for t in trades)
            has_sell = any(t.direction == "sell" for t in trades)
            if has_buy and has_sell:
                total_violation_groups += 1
                # 标记当天所有 buy 和 sell (整个 symbol 当天所有动作)
                note = f"同日有 {sum(1 for t in trades if t.direction == 'buy')} 笔 buy + {sum(1 for t in trades if t.direction == 'sell')} 笔 sell, 违反 A 股 T+1"
                for t in trades:
                    violations_by_trade[t.id] = note

        print(f"[Mark T+0] 3. T+0 违规 group (task,symbol,date): {total_violation_groups}")
        print(f"[Mark T+0] 4. 需标记的交易笔数: {len(violations_by_trade)}")

        if not violations_by_trade:
            print("[Mark T+0] 无违规, 退出")
            return

        # 写回
        for t in all_trades:
            if t.id in violations_by_trade:
                t.is_t0_violation = True
                t.t0_violation_note = violations_by_trade[t.id]
        db.commit()

        # 抽样输出
        print()
        print("=== 抽样 5 笔 T+0 违规 ===")
        samples = [t for t in all_trades if t.is_t0_violation][:5]
        for t in samples:
            print(f"  id={t.id} {t.trade_date} {t.symbol:10s} {t.direction:4s} | "
                  f"price={t.price:>8.2f} vol={t.volume:>6d} | {t.t0_violation_note[:60]}")

        # 任务汇总: 每个 task 的违规笔数
        print()
        print("=== 各任务 T+0 违规汇总 ===")
        by_task = defaultdict(lambda: {"total": 0, "violation": 0})
        for t in all_trades:
            by_task[t.task_id]["total"] += 1
            if t.is_t0_violation:
                by_task[t.task_id]["violation"] += 1
        for tid, c in by_task.items():
            ratio = c["violation"]/c["total"]*100 if c["total"] else 0
            print(f"  {tid[:8]} | {c['violation']:>4d} / {c['total']:>4d} ({ratio:5.1f}%)")

        print()
        print("[Mark T+0] ✅ 完成")
    except Exception as e:
        print(f"[Mark T+0] ❌ 失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
