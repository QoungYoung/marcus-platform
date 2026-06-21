# -*- coding: utf-8 -*-
"""
历史 trades 的 profit / profit_pct / net_profit 回填
=====================================================
问题: 2026-02-03 之后的所有 sell 在写入时 pre_pos.avg_price 取到 0,
     导致 profit=0/profit_pct=0 显示 (前端"交易明细"看不到真实收益)
     但 cost_value / position_value 都是正确的,只是 profit 字段错了

修复策略 (FIFO):
  1) 按 (symbol, trade_date, id) 排序
  2) 维护一个 FIFO 队列: 每次 buy 入队 (price, volume)
  3) 每次 sell 出队对应数量, profit = (sell_price - avg_fifo_cost) * volume
  4) 按上面的规则回填所有 sell 的 profit / profit_pct / net_profit

依赖: 同时回填 stamp_tax / transfer_fee (与 backfill_trade_fields.py 重复执行也无害)
"""
import sys, io
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from collections import defaultdict, deque
from sqlalchemy import asc

from app.database import SessionLocal, init_db
from app.models.backtest_orm import BacktestTrade


def is_sh(sym: str) -> bool:
    return sym.endswith(".SH")


def main():
    print("=" * 70)
    print("[Backfill Profit] 1. 应用 schema patches ...")
    init_db()

    db = SessionLocal()
    try:
        # 1) 按 (task_id, symbol) 分组, 每组按时间排序
        all_trades = db.query(BacktestTrade)\
            .order_by(asc(BacktestTrade.task_id), asc(BacktestTrade.symbol),
                      asc(BacktestTrade.trade_date), asc(BacktestTrade.id))\
            .all()
        print(f"[Backfill Profit] 2. 总交易: {len(all_trades)} 笔")

        if not all_trades:
            print("[Backfill Profit] 无数据, 退出")
            return

        # 2) 分组到 (task_id, symbol)
        groups = defaultdict(list)
        for t in all_trades:
            groups[(t.task_id, t.symbol)].append(t)

        # 3) FIFO 重新计算
        updated_sell = 0
        total_buy = 0
        total_sell = 0
        zero_profit_sell = 0
        net_profit_change = 0.0  # 回填前后净盈亏变化
        old_net_profit_total = 0.0
        new_net_profit_total = 0.0

        for (task_id, sym), trades in groups.items():
            fifo = deque()  # [(price, volume), ...]
            for t in trades:
                if t.direction == "buy":
                    fifo.append((float(t.price), int(t.volume)))
                    total_buy += 1
                elif t.direction == "sell":
                    total_sell += 1
                    sell_price = float(t.price)
                    sell_volume = int(t.volume)
                    # 扣减 FIFO 队列, 计算真实成本
                    remaining = sell_volume
                    total_cost = 0.0
                    matched_volume = 0
                    while remaining > 0 and fifo:
                        buy_price, buy_volume = fifo[0]
                        if buy_volume <= remaining:
                            # 整批消耗
                            total_cost += buy_price * buy_volume
                            matched_volume += buy_volume
                            remaining -= buy_volume
                            fifo.popleft()
                        else:
                            # 部分消耗
                            total_cost += buy_price * remaining
                            matched_volume += remaining
                            # 队列头 buy_volume -= remaining, 更新后入队
                            fifo[0] = (buy_price, buy_volume - remaining)
                            remaining = 0
                    if matched_volume > 0 and total_cost > 0:
                        avg_cost = total_cost / matched_volume
                        new_profit = round((sell_price - avg_cost) * sell_volume, 2)
                        new_profit_pct = round((sell_price / avg_cost - 1) * 100, 4) if avg_cost > 0 else 0
                    else:
                        # 卖空 / 无 buy 配对 → profit=0
                        new_profit = 0.0
                        new_profit_pct = 0.0
                        zero_profit_sell += 1

                    # 费用拆分
                    amount = sell_price * sell_volume
                    stamp_tax = round(amount * 0.001, 2)
                    transfer_fee = round(amount * 0.00001, 2) if is_sh(sym) else 0.0
                    # 净盈亏 = 毛盈亏 - 印花税 - 过户费
                    new_net_profit = round(new_profit - stamp_tax - transfer_fee, 2)

                    # 累计变化
                    old_net_profit_total += float(t.net_profit or 0)
                    new_net_profit_total += new_net_profit
                    if abs(new_profit - float(t.profit or 0)) > 0.01:
                        updated_sell += 1

                    # 写回数据库
                    t.profit = new_profit
                    t.profit_pct = new_profit_pct
                    t.stamp_tax = stamp_tax
                    t.transfer_fee = transfer_fee
                    t.net_profit = new_net_profit
                    # actual_price 冗余字段补全
                    if not t.actual_price or t.actual_price == 0:
                        t.actual_price = sell_price

        # 4) 一次性 commit
        db.commit()
        net_profit_change = new_net_profit_total - old_net_profit_total

        print()
        print("=" * 70)
        print(f"[Backfill Profit] 3. buy: {total_buy} 笔, sell: {total_sell} 笔")
        print(f"[Backfill Profit] 4. 更新的 sell: {updated_sell} 笔")
        print(f"[Backfill Profit] 5. 卖空/无配对的 sell: {zero_profit_sell} 笔 (profit 保持 0)")
        print()
        print(f"[Backfill Profit] 6. 净盈亏变化:")
        print(f"     旧净盈亏:     ¥{old_net_profit_total:>15,.2f}")
        print(f"     新净盈亏:     ¥{new_net_profit_total:>15,.2f}")
        print(f"     差值:         ¥{net_profit_change:>+15,.2f}")
        print()
        print("[Backfill Profit] ✅ 完成")

    except Exception as e:
        print(f"[Backfill Profit] ❌ 失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
