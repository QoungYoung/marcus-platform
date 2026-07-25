#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回填 paper_daily_snapshot — 用 Tushare 历史收盘价重算每日真实净值。

用法:
    python scripts/backfill_daily_snapshots.py          # 回填全部历史
    python scripts/backfill_daily_snapshots.py --days 30  # 只回填最近30个交易日
    python scripts/backfill_daily_snapshots.py --dry-run  # 预览，不写入
"""
import sys
import os
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# 添加项目路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT / "core"))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import tushare as ts
from app.database import SessionLocal
from app.config import get_settings

# 费率常量
_BUY_COMMISSION = 0.0005
_SELL_FEE_RATE = 0.0015

settings = get_settings()


def get_tushare_pro():
    """获取 Tushare pro 实例"""
    token = os.getenv("TUSHARE_TOKEN", "")
    if not token:
        raise EnvironmentError("TUSHARE_TOKEN 未配置")
    pro = ts.pro_api(token)
    api_url = os.getenv("TUSHARE_API_URL", "")
    if api_url:
        pro._DataApi__http_url = api_url
    return pro


def get_all_trade_dates(db) -> list:
    """获取所有有交易的日期"""
    from app.models.paper_trade import PaperTrade
    from sqlalchemy import func
    rows = (
        db.query(func.substr(PaperTrade.created_at, 1, 10))
        .filter((PaperTrade.voided == 0) | (PaperTrade.voided == None))
        .distinct()
        .order_by(func.substr(PaperTrade.created_at, 1, 10))
        .all()
    )
    return [r[0] for r in rows if r[0]]


def get_trades_up_to(db, target_date: str) -> list:
    """获取截至 target_date 的所有有效交易，按时序排列"""
    from app.models.paper_trade import PaperTrade
    from sqlalchemy import func
    return (
        db.query(PaperTrade)
        .filter(
            (PaperTrade.voided == 0) | (PaperTrade.voided == None),
            (PaperTrade.trade_date <= target_date)
            | ((PaperTrade.trade_date == None) & (func.substr(PaperTrade.created_at, 1, 10) <= target_date)),
        )
        .order_by(
            func.coalesce(PaperTrade.trade_date, func.substr(PaperTrade.created_at, 1, 10)),
            PaperTrade.id,
        )
        .all()
    )


def get_realized_pnl(db, target_date: str) -> float:
    """截至 target_date 的累计已实现盈亏"""
    from app.models.paper_trade import PaperTrade
    from sqlalchemy import func
    return float(
        db.query(func.coalesce(func.sum(PaperTrade.profit), 0))
        .filter(
            PaperTrade.direction == "卖出",
            (PaperTrade.voided == 0) | (PaperTrade.voided == None),
            (PaperTrade.trade_date <= target_date)
            | ((PaperTrade.trade_date == None) & (func.substr(PaperTrade.created_at, 1, 10) <= target_date)),
        )
        .scalar()
        or 0
    )


def symbol_to_ts_code(symbol: str) -> str:
    """Marcus 代码 SH600900 → Tushare ts_code 600900.SH"""
    code = symbol[2:] if (symbol.startswith("SH") or symbol.startswith("SZ") or symbol.startswith("BJ")) else symbol
    if symbol.startswith("SH") or (code.startswith("6")):
        return f"{code}.SH"
    elif symbol.startswith("SZ") or code.startswith(("0", "3")):
        return f"{code}.SZ"
    elif symbol.startswith("BJ") or code.startswith(("8", "4")):
        return f"{code}.BJ"
    return f"{code}.SH"


def fetch_close_prices(pro, symbols: list, trade_date: str) -> dict:
    """从 Tushare 获取指定日期的收盘价。

    Returns: {marcus_symbol: close_price}
    """
    if not symbols:
        return {}

    ts_codes = [symbol_to_ts_code(s) for s in symbols]
    code_to_marcus = {ts: ms for ts, ms in zip(ts_codes, symbols)}

    # Tushare daily 需要 YYYYMMDD 格式
    date_str = trade_date.replace("-", "")

    try:
        df = pro.daily(
            ts_code=",".join(ts_codes),
            trade_date=date_str,
            fields="ts_code,trade_date,close",
        )
    except Exception as e:
        print(f"  [WARN] Tushare daily({date_str}) 请求失败: {e}")
        return {}

    if df is None or df.empty:
        # 尝试逐个查询（某些日期批量查可能为空）
        result = {}
        for ts_code in ts_codes:
            try:
                single_df = pro.daily(
                    ts_code=ts_code,
                    trade_date=date_str,
                    fields="ts_code,trade_date,close",
                )
                if single_df is not None and not single_df.empty:
                    result[code_to_marcus[ts_code]] = float(single_df.iloc[0]["close"])
            except Exception:
                pass
        return result

    result = {}
    for _, row in df.iterrows():
        ts_code = row["ts_code"]
        marcus = code_to_marcus.get(ts_code)
        if marcus:
            result[marcus] = float(row["close"])
    return result


def fifo_replay(trades: list, initial_capital: float) -> tuple:
    """FIFO 重放交易，返回 (available_cash, positions_dict)

    positions_dict: {symbol: [{'price': avg_entry, 'volume': qty}, ...]}
    """
    cash = initial_capital
    positions = {}

    for t in trades:
        sym = t.symbol if hasattr(t, "symbol") else t["symbol"]
        direction = t.direction if hasattr(t, "direction") else t["direction"]
        price = float(t.price) if hasattr(t, "price") else float(t["price"])
        volume = int(t.volume) if hasattr(t, "volume") else int(t["volume"])

        if direction == "买入":
            cost = price * volume * (1 + _BUY_COMMISSION)
            cash -= cost
            positions.setdefault(sym, []).append({"price": price, "volume": volume})
        elif direction == "卖出":
            lots = positions.get(sym, [])
            if not lots:
                continue
            gross = price * volume
            sell_fee = gross * _SELL_FEE_RATE
            cash += gross - sell_fee
            remaining = volume
            i = 0
            while remaining > 0 and i < len(lots):
                used = min(lots[i]["volume"], remaining)
                lots[i]["volume"] -= used
                remaining -= used
                if lots[i]["volume"] == 0:
                    lots.pop(i)
                else:
                    i += 1

    return cash, positions


def build_position_list(positions: dict) -> list:
    """从 positions dict 构建 position_list"""
    result = []
    for sym, lots in positions.items():
        if not lots:
            continue
        total_vol = sum(l["volume"] for l in lots)
        if total_vol <= 0:
            continue
        avg_price = sum(l["price"] * l["volume"] for l in lots) / total_vol
        result.append({"symbol": sym, "volume": total_vol, "avg_price": avg_price})
    return result


def get_initial_capital(db) -> float:
    """获取初始资金"""
    from app.models.paper_trade import PaperAccountInfo
    acct = db.query(PaperAccountInfo).filter(PaperAccountInfo.id == 1).first()
    if acct:
        return float(acct.initial_capital)
    return 100000.0


def is_trade_day(pro, date_str: str) -> bool:
    """判断是否为交易日（通过 Tushare trade_cal 查询）"""
    try:
        dt = date_str.replace("-", "")
        df = pro.trade_cal(exchange="SSE", start_date=dt, end_date=dt, is_open="1")
        return df is not None and not df.empty
    except Exception:
        # 兜底：周一到周五
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.weekday() < 5


def backfill(dry_run: bool = False, max_days: Optional[int] = None):
    """主回填逻辑"""
    pro = get_tushare_pro()
    db = SessionLocal()

    try:
        all_dates = get_all_trade_dates(db)
        if not all_dates:
            print("没有找到任何交易记录")
            return

        initial_cap = get_initial_capital(db)
        print(f"初始资金: {initial_cap:,.2f}")
        print(f"交易日范围: {all_dates[0]} → {all_dates[-1]}")
        print(f"共 {len(all_dates)} 个交易日")

        if max_days:
            all_dates = all_dates[-max_days:]
            print(f"限制最近 {max_days} 天")

        print()
        print(f"{'日期':<12} {'持仓数':<6} {'持仓成本':>10} {'持仓市值':>10} {'总资产':>12} {'浮盈':>10} {'已实现':>10}")
        print("-" * 82)

        # 按交易日递增顺序，逐个回放
        # 优化：只在交易日做快照，非交易日跳过
        updated = 0
        skipped = 0

        for target_date in all_dates:
            # 获取截至该日的交易
            trades = get_trades_up_to(db, target_date)
            if not trades:
                skipped += 1
                continue

            # FIFO 回放
            available_cash, positions = fifo_replay(trades, initial_cap)
            position_list = build_position_list(positions)
            realized_pnl = get_realized_pnl(db, target_date)

            # 获取历史收盘价
            held_symbols = [p["symbol"] for p in position_list]
            close_prices = fetch_close_prices(pro, held_symbols, target_date)

            # 按收盘价计算持仓市值
            position_value = 0.0
            cost_value = 0.0
            priced_count = 0
            for p in position_list:
                cost = p["avg_price"] * p["volume"]
                cost_value += cost
                cp = close_prices.get(p["symbol"])
                if cp and cp > 0:
                    position_value += cp * p["volume"]
                    priced_count += 1
                else:
                    # 没有收盘价：回退到成本价
                    position_value += cost

            total_asset = available_cash + position_value
            float_pnl = position_value - cost_value
            total_pnl = total_asset - initial_cap

            status = f"[{priced_count}/{len(position_list)} 有收盘价]" if position_list else ""
            print(
                f"{target_date:<12} {len(position_list):<6} {cost_value:>10,.2f} "
                f"{position_value:>10,.2f} {total_asset:>12,.2f} "
                f"{float_pnl:>+10,.2f} {realized_pnl:>+10,.2f} {status}"
            )

            if dry_run:
                continue

            # Upsert 到 paper_daily_snapshot
            from app.models.paper_trade import PaperDailySnapshot
            snap = db.query(PaperDailySnapshot).filter(
                PaperDailySnapshot.trade_date == target_date
            ).first()
            if snap:
                snap.total_asset = total_asset
                snap.available_cash = available_cash
                snap.frozen_cash = 0.0
                snap.position_value = position_value
                snap.cost_value = cost_value
                snap.realized_pnl = realized_pnl
                snap.float_pnl = float_pnl
                snap.total_pnl = total_pnl
                snap.initial_capital = initial_cap
                snap.created_at = datetime.now().isoformat()
            else:
                db.add(PaperDailySnapshot(
                    trade_date=target_date,
                    total_asset=total_asset,
                    available_cash=available_cash,
                    frozen_cash=0.0,
                    position_value=position_value,
                    cost_value=cost_value,
                    realized_pnl=realized_pnl,
                    float_pnl=float_pnl,
                    total_pnl=total_pnl,
                    initial_capital=initial_cap,
                    created_at=datetime.now().isoformat(),
                ))
            db.commit()
            updated += 1

    finally:
        db.close()

    print()
    if dry_run:
        print(f"[DRY RUN] 预览完成，共 {len(all_dates)} 天（跳过 {skipped} 天）")
    else:
        print(f"回填完成：更新 {updated} 天，跳过 {skipped} 天（无交易）")


def main():
    parser = argparse.ArgumentParser(description="回填 paper_daily_snapshot 历史净值")
    parser.add_argument("--dry-run", action="store_true", help="预览不写入")
    parser.add_argument("--days", type=int, default=None, help="只回填最近 N 天")
    args = parser.parse_args()
    backfill(dry_run=args.dry_run, max_days=args.days)


if __name__ == "__main__":
    main()
