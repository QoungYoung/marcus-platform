# -*- coding: utf-8 -*-
"""
历史回测 trades 字段回填脚本
=============================
场景: 在新增 stamp_tax / transfer_fee / net_profit 等列之前已经回测过的任务,
     数据库里这些列是 0。运行此脚本按规则补全:

回填规则:
  - stamp_tax (印花税) = amount * 0.001, 仅 direction='sell'
  - transfer_fee (过户费) = amount * 0.00001, 仅 direction='sell' 且 symbol 以 .SH 结尾
  - net_profit (净盈亏) = profit - stamp_tax - transfer_fee, 仅 direction='sell'
  - actual_price = price (冗余字段,直接用现有 price)
  - 其他字段 (phase_time / signal_price / slippage_pct) 无历史数据,保持 0
"""
import sys
import io

# 强制 UTF-8 输出 (Windows GBK 终端无法打印 ¥ 字符)
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from sqlalchemy import text

from app.database import SessionLocal, engine, init_db
from app.models.backtest_orm import BacktestTrade


def main():
    # 1) 先确保 schema patch 已应用 (idempotent)
    print("=" * 60)
    print("[Backfill] 1. 应用 schema patches (确保新列存在)...")
    init_db()

    db = SessionLocal()
    try:
        # 2) 统计待回填数量
        total = db.query(BacktestTrade).count()
        sells = db.query(BacktestTrade)\
            .filter(BacktestTrade.direction == "sell").count()
        sh_sells = db.query(BacktestTrade)\
            .filter(BacktestTrade.direction == "sell")\
            .filter(BacktestTrade.symbol.like("%.SH")).count()
        print(f"[Backfill] 2. trades 总数: {total} (其中 sell: {sells}, sell.SH: {sh_sells})")

        if total == 0:
            print("[Backfill] 没有 trades 数据,无需回填,退出")
            return

        # 3) 逐行回填 (避免单条 SQL 太长)
        #    用 raw SQL 一次性 UPDATE 效率更高:
        with engine.begin() as conn:
            # 3a. sell: stamp_tax = amount * 0.001
            r1 = conn.execute(text("""
                UPDATE backtest_trades
                SET stamp_tax = ROUND(CAST(amount * 0.001 AS numeric), 2)::float
                WHERE direction = 'sell' AND (stamp_tax IS NULL OR stamp_tax = 0)
            """))
            print(f"[Backfill] 3a. stamp_tax 补全: {r1.rowcount} 行")

            # 3b. sell.SH: transfer_fee = amount * 0.00001
            r2 = conn.execute(text("""
                UPDATE backtest_trades
                SET transfer_fee = ROUND(CAST(amount * 0.00001 AS numeric), 2)::float
                WHERE direction = 'sell'
                  AND symbol LIKE '%.SH'
                  AND (transfer_fee IS NULL OR transfer_fee = 0)
            """))
            print(f"[Backfill] 3b. transfer_fee 补全: {r2.rowcount} 行")

            # 3c. sell: actual_price = price (冗余字段)
            r3 = conn.execute(text("""
                UPDATE backtest_trades
                SET actual_price = price
                WHERE (actual_price IS NULL OR actual_price = 0) AND price > 0
            """))
            print(f"[Backfill] 3c. actual_price 补全: {r3.rowcount} 行")

            # 3d. sell: net_profit = profit - stamp_tax - transfer_fee
            r4 = conn.execute(text("""
                UPDATE backtest_trades
                SET net_profit = ROUND(CAST(profit - stamp_tax - transfer_fee AS numeric), 2)::float
                WHERE direction = 'sell'
            """))
            print(f"[Backfill] 3d. net_profit 补全: {r4.rowcount} 行")

        # 4) 验证结果
        print()
        print("=" * 60)
        print("[Backfill] 4. 验证回填结果 (抽样 3 条 sell):")
        samples = db.query(BacktestTrade)\
            .filter(BacktestTrade.direction == "sell")\
            .order_by(BacktestTrade.trade_date.desc())\
            .limit(3).all()
        for s in samples:
            print(f"  {s.trade_date} {s.symbol} {s.direction} | "
                  f"amount={s.amount:.2f} | "
                  f"commission={s.commission:.2f} | "
                  f"stamp_tax={s.stamp_tax:.2f} | "
                  f"transfer_fee={s.transfer_fee:.2f} | "
                  f"profit={s.profit:.2f} | "
                  f"net_profit={s.net_profit:.2f}")

        # 5) 整体统计
        from sqlalchemy import func
        agg = db.query(
            func.coalesce(func.sum(BacktestTrade.stamp_tax), 0).label("total_stamp"),
            func.coalesce(func.sum(BacktestTrade.transfer_fee), 0).label("total_transfer"),
            func.coalesce(func.sum(BacktestTrade.commission), 0).label("total_commission"),
            func.coalesce(func.sum(BacktestTrade.profit), 0).label("total_profit"),
            func.coalesce(func.sum(BacktestTrade.net_profit), 0).label("total_net"),
        ).filter(BacktestTrade.direction == "sell").first()
        print()
        print(f"  [累计] sell 笔数: {agg and (db.query(BacktestTrade).filter(BacktestTrade.direction=='sell').count())}")
        print(f"  [累计] 印花税:     ¥{float(agg.total_stamp):>12,.2f}")
        print(f"  [累计] 过户费:     ¥{float(agg.total_transfer):>12,.2f}")
        print(f"  [累计] 手续费:     ¥{float(agg.total_commission):>12,.2f} (已含印花税)")
        print(f"  [累计] 毛盈亏:     ¥{float(agg.total_profit):>12,.2f}")
        print(f"  [累计] 净盈亏:     ¥{float(agg.total_net):>12,.2f}")
        print()
        print("[Backfill] ✅ 完成")

    except Exception as e:
        print(f"[Backfill] ❌ 失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
