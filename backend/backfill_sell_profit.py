"""
回补 sell 交易的 profit / profit_pct
从 PaperTradingEngine 的 SQLite trades.db 读取引擎已算好的 FIFO profit,
同步到 PG backtest_trades 表。

根因: backtest.py 用英文 "sell" 匹配引擎的中文 "卖出", 导致 profit 全为 0.

用法:
    python backend/backfill_sell_profit.py <task_id>
    python backend/backfill_sell_profit.py 81b3f48b-606
"""
import sys
import os
import sqlite3
from pathlib import Path
from datetime import date

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

def main(task_id: str):
    # 1. 引擎 SQLite 路径
    engine_db = PROJECT_ROOT / "data" / "backtest" / task_id / "paper" / "trades.db"
    if not engine_db.exists():
        print(f" 引擎 SQLite 不存在: {engine_db}")
        print("   回测可能已被清理或未使用此引擎")
        return

    conn_sql = sqlite3.connect(str(engine_db))
    conn_sql.row_factory = sqlite3.Row
    engine_trades = conn_sql.execute(
        "SELECT * FROM trades ORDER BY created_at"
    ).fetchall()
    conn_sql.close()
    print(f"[SQLite] engine trades: {len(engine_trades)} records")

    # 2. PG 连接
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))
    from app.database import SessionLocal
    from app.models.backtest_orm import BacktestTrade

    db = SessionLocal()
    try:
        pg_trades = (
            db.query(BacktestTrade)
            .filter(BacktestTrade.task_id == task_id)
            .order_by(BacktestTrade.trade_date, BacktestTrade.id)
            .all()
        )
        print(f" PG backtest_trades: {len(pg_trades)} 条")

        # 符号格式归一化: "002129.SZ" <-> "SZ002129"
        def norm_sym(s):
            s = s.strip().upper()
            if s.endswith(".SH"): return "SH" + s[:-3]
            if s.endswith(".SZ"): return "SZ" + s[:-3]
            if s.startswith("SH") and len(s) == 8: return s[2:] + ".SH"
            if s.startswith("SZ") and len(s) == 8: return s[2:] + ".SZ"
            return s

        # 3. 建立引擎 sell trades 的索引: (norm(symbol), date) -> [...]
        from collections import defaultdict
        engine_sells = defaultdict(list)
        for et in engine_trades:
            if et["direction"] == "卖出":
                sym = norm_sym(et["symbol"])
                created = et["created_at"]
                dt = created[:10] if created else "unknown"
                engine_sells[(sym, dt)].append({
                    "price": et["price"],
                    "volume": et["volume"],
                    "profit": et["profit"],
                    "amount": et["amount"],
                    "row": et,
                })

        updated = 0
        skipped = 0
        for pt in pg_trades:
            if pt.direction != "sell":
                continue

            sym = norm_sym(pt.symbol)
            td = pt.trade_date.isoformat() if isinstance(pt.trade_date, date) else str(pt.trade_date)[:10]

            candidates = engine_sells.get((sym, td), [])
            if not candidates:
                # 尝试只用 symbol 匹配(日期格式可能不同)
                for k in engine_sells:
                    if k[0] == sym:
                        candidates = engine_sells[k]
                        break

            fifo_profit = None
            sell_amount = pt.price * pt.volume

            if candidates:
                # 匹配最接近的(按 price * volume 最接近)
                pg_amount = pt.price * pt.volume
                best = min(candidates, key=lambda c: abs(c["price"] * c["volume"] - pg_amount))
                fifo_profit = float(best["profit"] or 0)
                sell_amount = float(best["amount"] or sell_amount)

            # 引擎无匹配 -> FIFO 手工回算
            if fifo_profit is None:
                prev_buys = (
                    db.query(BacktestTrade)
                    .filter(
                        BacktestTrade.task_id == task_id,
                        BacktestTrade.symbol == pt.symbol,
                        BacktestTrade.direction == "buy",
                        BacktestTrade.id < pt.id,
                    )
                    .order_by(BacktestTrade.id)
                    .all()
                )
                prev_sells = (
                    db.query(BacktestTrade)
                    .filter(
                        BacktestTrade.task_id == task_id,
                        BacktestTrade.symbol == pt.symbol,
                        BacktestTrade.direction == "sell",
                        BacktestTrade.id < pt.id,
                    )
                    .order_by(BacktestTrade.id)
                    .all()
                )
                lots = [[b.volume, float(b.price)] for b in prev_buys]
                for ps in prev_sells:
                    remaining = ps.volume
                    i = 0
                    while remaining > 0 and i < len(lots):
                        used = min(lots[i][0], remaining)
                        remaining -= used
                        lots[i][0] -= used
                        if lots[i][0] == 0:
                            lots.pop(i)
                        else:
                            i += 1
                remaining = pt.volume
                i = 0
                cost = 0.0
                while remaining > 0 and i < len(lots):
                    used = min(lots[i][0], remaining)
                    cost += used * lots[i][1]
                    lots[i][0] -= used
                    remaining -= used
                    if lots[i][0] == 0:
                        lots.pop(i)
                    else:
                        i += 1
                if remaining > 0:
                    print(f"   {pt.id}: {sym} {td} FIFO不足(缺{remaining}股)")
                    skipped += 1
                    continue
                fifo_profit = sell_amount - cost
                print(f"   [FIFO] {sym} {td}: cost={cost:.2f}, profit={fifo_profit:.2f}")
            else:
                print(f"   [Engine] {sym} {td}: matched")
            
            # 净盈亏 = FIFO profit - 印花税(0.1%) - 手续费(0.05%)
            stamp_tax = sell_amount * 0.001
            commission = sell_amount * 0.0005
            net_profit = round(fifo_profit - stamp_tax - commission, 2)
            profit_pct = round(fifo_profit / sell_amount * 100, 4) if sell_amount > 0 else 0
            
            old_profit = pt.profit

            if abs(old_profit - net_profit) < 0.01 and abs((pt.profit_pct or 0) - profit_pct) < 0.01:
                skipped += 1
                continue

            pt.profit = net_profit
            pt.profit_pct = profit_pct
            updated += 1
            print(f"   {sym} {td} sell {pt.volume}股@{pt.price:.2f}: "
                  f"profit {old_profit}{net_profit}, pct {pt.profit_pct}% "
                  f"(FIFO={fifo_profit}, stamp={stamp_tax:.2f}, comm={commission:.2f})")

        if updated > 0:
            db.commit()
            print(f"\n 已更新 {updated} 条 sell 记录")
        else:
            print(f"\n 无需更新 (所有 profit 已正确)")

        if skipped > 0:
            print(f" 跳过 {skipped} 条 (无匹配或无变化)")

    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python backend/backfill_sell_profit.py <task_id>")
        sys.exit(1)
    main(sys.argv[1])
