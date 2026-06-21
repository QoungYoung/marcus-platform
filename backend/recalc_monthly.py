"""
重新计算月度绩效 (基于已回补的 sell profit 数据)

用法: python backend/recalc_monthly.py <task_id>
"""
import sys
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.database import SessionLocal
from app.models.backtest_orm import BacktestTrade, BacktestMonthlyMetric
from app.services.local_data_provider import local_data


def main(task_id: str):
    db = SessionLocal()
    try:
        # 1. 读取所有 sell trades
        sells = (
            db.query(BacktestTrade)
            .filter(
                BacktestTrade.task_id == task_id,
                BacktestTrade.direction == "sell",
            )
            .order_by(BacktestTrade.trade_date)
            .all()
        )
        print(f"Sell trades: {len(sells)}")

        # 2. 按月份聚合
        monthly = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0, "returns": [], "max_dd": 0.0})
        for s in sells:
            month = s.trade_date.strftime("%Y-%m")
            m = monthly[month]
            m["total"] += 1
            if s.profit > 0:
                m["wins"] += 1
            elif s.profit < 0:
                m["losses"] += 1

        # 3. 更新 DB
        updated = 0
        for month, data in sorted(monthly.items()):
            wins = data["wins"]
            losses = data["losses"]
            total = wins + losses
            win_rate = round(wins / total * 100, 2) if total > 0 else 0

            existing = (
                db.query(BacktestMonthlyMetric)
                .filter(
                    BacktestMonthlyMetric.task_id == task_id,
                    BacktestMonthlyMetric.month == month,
                )
                .first()
            )

            if existing:
                old_wr = existing.win_rate or 0
                if abs(old_wr - win_rate) > 0.01:
                    existing.win_rate = win_rate
                    existing.win_count = wins
                    updated += 1
                    print(f"  {month}: win_rate {old_wr:.1f}% -> {win_rate:.1f}% (wins={wins}, losses={losses})")
                else:
                    print(f"  {month}: win_rate {win_rate:.1f}% unchanged")

        if updated:
            db.commit()
            print(f"\nUpdated {updated} monthly records")
        else:
            print("\nAll monthly metrics already correct")

    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python backend/recalc_monthly.py <task_id>")
        sys.exit(1)
    main(sys.argv[1])
