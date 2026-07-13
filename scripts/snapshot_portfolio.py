#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daily portfolio snapshot script.

Called by the scheduler at market close (15:01 on weekdays).
Computes current total_asset with real-time market prices and persists
to trades.db::daily_snapshot.

Usage:
    python snapshot_portfolio.py [--date YYYY-MM-DD] [--db path/to/trades.db]
"""
import sqlite3
import sys
import os
import argparse
from datetime import datetime
from pathlib import Path

# Fee constants (mirror paper_engine.py / portfolio.py)
BUY_COMMISSION = 0.0005
SELL_FEE_RATE = 0.0015

# Add project paths for Xueqiu engine import
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "apps" / "paper-trading"))


def get_realtime_prices(symbols: list) -> dict:
    """Fetch real-time prices from Xueqiu. Returns {symbol: float, ...}."""
    try:
        from xueqiu_engine import XueqiuEngine
        config_path = PROJECT_ROOT / "core" / "config.json"
        if not config_path.exists():
            print("[snapshot] Xueqiu config not found", file=sys.stderr)
            return {}
        engine = XueqiuEngine(config_file=str(config_path))
        prices = {}
        for sym in symbols:
            try:
                quote = engine.get_stock_quote(sym)
                if quote and quote.get('current'):
                    prices[sym] = quote['current']
            except Exception:
                pass
        return prices
    except Exception as e:
        print(f"[snapshot] Xueqiu fetch failed: {e}", file=sys.stderr)
        return {}


def save_snapshot(target_date: str, db_path: str):
    """Core snapshot logic (standalone, no backend dependency)."""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    curs = conn.cursor()

    # Ensure table exists
    curs.execute('''
        CREATE TABLE IF NOT EXISTS daily_snapshot (
            trade_date TEXT PRIMARY KEY,
            total_asset REAL NOT NULL,
            available_cash REAL NOT NULL,
            frozen_cash REAL DEFAULT 0,
            position_value REAL DEFAULT 0,
            cost_value REAL DEFAULT 0,
            realized_pnl REAL DEFAULT 0,
            float_pnl REAL DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            initial_capital REAL NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')

    # Read account info
    curs.execute("SELECT initial_capital, frozen_cash FROM account_info WHERE id=1")
    row = curs.fetchone()
    if not row:
        print("[snapshot] ERROR: No account_info found", file=sys.stderr)
        conn.close()
        sys.exit(1)
    initial_cap = row[0]
    frozen_cash = row[1] or 0.0

    # Read all trades up to target_date
    curs.execute("""
        SELECT symbol, direction, price, volume, trade_date, created_at
        FROM trades
        WHERE (voided = 0 OR voided IS NULL)
          AND (trade_date <= ? OR (trade_date IS NULL AND DATE(created_at) <= ?))
        ORDER BY COALESCE(trade_date, DATE(created_at)), id
    """, (target_date, target_date))
    trades = curs.fetchall()

    # Get realized PnL up to target_date
    curs.execute("""
        SELECT COALESCE(SUM(profit), 0) FROM trades
        WHERE direction='卖出'
          AND (trade_date <= ? OR (trade_date IS NULL AND DATE(created_at) <= ?))
    """, (target_date, target_date))
    realized_pnl = curs.fetchone()[0]

    # FIFO replay
    available_cash = initial_cap
    positions_lots = {}

    for sym, direction, price, volume, td, ca in trades:
        if direction == '买入':
            cost = price * volume * (1 + BUY_COMMISSION)
            available_cash -= cost
            positions_lots.setdefault(sym, []).append({'price': price, 'volume': volume})
        elif direction == '卖出':
            lots = positions_lots.get(sym, [])
            if not lots:
                continue
            gross = price * volume
            sell_fee = gross * SELL_FEE_RATE
            available_cash += gross - sell_fee
            remaining = volume
            i = 0
            while remaining > 0 and i < len(lots):
                used = min(lots[i]['volume'], remaining)
                lots[i]['volume'] -= used
                remaining -= used
                if lots[i]['volume'] == 0:
                    lots.pop(i)
                else:
                    i += 1

    # Build position list
    positions = []
    for sym, lots in positions_lots.items():
        if not lots:
            continue
        total_vol = sum(l['volume'] for l in lots)
        avg_price = sum(l['price'] * l['volume'] for l in lots) / total_vol
        positions.append({'symbol': sym, 'volume': total_vol, 'avg_price': avg_price})

    # Valuation at market prices
    symbols = [p['symbol'] for p in positions]
    prices = get_realtime_prices(symbols) if symbols else {}
    price_source = 'cost'
    position_value = 0.0

    for p in positions:
        market_price = prices.get(p['symbol'], p['avg_price'])
        position_value += market_price * p['volume']

    if prices:
        price_source = 'market'

    cost_value = sum(p['avg_price'] * p['volume'] for p in positions)
    total_asset = available_cash + frozen_cash + position_value
    float_pnl = position_value - cost_value
    total_pnl = total_asset - initial_cap

    # Insert/replace
    now = datetime.now().isoformat()
    curs.execute('''
        INSERT OR REPLACE INTO daily_snapshot
        (trade_date, total_asset, available_cash, frozen_cash, position_value,
         cost_value, realized_pnl, float_pnl, total_pnl, initial_capital, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (target_date, total_asset, available_cash, frozen_cash, position_value,
          cost_value, realized_pnl, float_pnl, total_pnl, initial_cap, now))

    conn.commit()
    conn.close()

    print(f"[snapshot] {target_date} | total_asset={total_asset:,.2f} | "
          f"positions={len(positions)} | valuation={price_source}")
    return total_asset


def main():
    parser = argparse.ArgumentParser(description='Daily portfolio snapshot')
    parser.add_argument('--date', default=None, help='Target date YYYY-MM-DD (default: today)')
    parser.add_argument('--db', default=None, help='Path to trades.db')
    args = parser.parse_args()

    target_date = args.date or datetime.now().strftime('%Y-%m-%d')
    db_path = args.db or str(PROJECT_ROOT / "data" / "trades.db")

    if not os.path.exists(db_path):
        print(f"[snapshot] ERROR: trades.db not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    total = save_snapshot(target_date, db_path)
    print(f"[snapshot] Done. total_asset={total:,.2f}")


if __name__ == '__main__':
    main()
