# -*- coding: utf-8 -*-
"""
Portfolio API endpoints.
"""
import sys
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi import Query

from app.config import get_settings
from app.models.account import AccountResponse, PositionResponse, PortfolioSummary

settings = get_settings()

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


# Stock name cache
_stock_name_cache = {}
# Stock price cache (short TTL)
_stock_price_cache = {}
_price_cache_time = 0


def get_stock_name(symbol: str) -> str:
    """Get stock name from symbol, query stock_pool.db for Chinese name."""
    if symbol in _stock_name_cache:
        return _stock_name_cache[symbol]

    # Try to get from stock_pool.db
    pool_db = settings.data_dir / "stock_pool.db"
    if pool_db.exists():
        try:
            conn = sqlite3.connect(str(pool_db))
            conn.row_factory = sqlite3.Row
            curs = conn.cursor()

            # Extract numeric code from symbol (e.g., "SH600519" -> "600519")
            code = symbol[2:] if len(symbol) > 4 and symbol[:2] in ('SH', 'SZ', 'BJ') else symbol

            curs.execute("SELECT name FROM stock_pool WHERE symbol = ? OR ts_code = ?",
                                (code, symbol))
            row = curs.fetchone()
            conn.close()

            if row and row['name']:
                # Clean the name - remove any remaining code prefix
                name = row['name'].strip()
                _stock_name_cache[symbol] = name
                return name
        except Exception as e:
            print(f"Error querying stock name: {e}")

    # Fallback to symbol
    _stock_name_cache[symbol] = symbol
    return symbol


def get_realtime_prices(symbols: list) -> dict:
    """Fetch real-time stock prices from Xueqiu (non-blocking)."""
    global _stock_price_cache, _price_cache_time
    import time as _time
    import concurrent.futures

    # Cache for 30 seconds to avoid excessive API calls
    now = _time.time()
    if _stock_price_cache and (now - _price_cache_time) < 30:
        missing = [s for s in symbols if s not in _stock_price_cache]
        if not missing:
            return _stock_price_cache
    else:
        _stock_price_cache = {}

    try:
        # Xueqiu engine is in marcus-platform/core/
        xueqiu_dir = settings.workspace_path / "core"
        xueqiu_config = xueqiu_dir / "config.json"
        if not xueqiu_config.exists():
            return _stock_price_cache

        sys.path.insert(0, str(xueqiu_dir))
        from xueqiu_engine import XueqiuEngine
        engine = XueqiuEngine(config_file=str(xueqiu_config))

        def _fetch_one(symbol):
            try:
                quote = engine.get_stock_quote(symbol)
                if quote and quote.get('current'):
                    return symbol, quote.get('current')
            except Exception:
                pass
            return symbol, None

        # Use thread pool with 5s timeout to avoid blocking
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(_fetch_one, s): s for s in symbols}
            for future in concurrent.futures.as_completed(futures, timeout=5):
                try:
                    symbol, price = future.result(timeout=3)
                    if price is not None:
                        _stock_price_cache[symbol] = price
                except concurrent.futures.TimeoutError:
                    print(f"[Portfolio] Timeout fetching price for {futures[future]}")
                except Exception:
                    pass

        _price_cache_time = now
    except concurrent.futures.TimeoutError:
        print("[Portfolio] Xueqiu batch fetch timed out")
    except Exception as e:
        print(f"[Portfolio] Xueqiu fetch failed: {e}")

    return _stock_price_cache


def calculate_positions_from_db():
    """Calculate current positions from trades.db using FIFO."""
    import sqlite3

    db_file = settings.data_dir / "trades.db"
    if not db_file.exists():
        return [], {"available_cash": 0, "initial_capital": 1000000}

    conn = sqlite3.connect(str(db_file), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    curs = conn.cursor()

    # Get account info
    curs.execute("SELECT * FROM account_info WHERE id=1")
    account_row = curs.fetchone()
    if not account_row:
        account = {"initial_capital": 1000000, "available_cash": 1000000, "frozen_cash": 0}
    else:
        account = dict(account_row)

    # Get all trades in FIFO order (by created_at for chronological ordering)
    curs.execute("SELECT id, symbol, direction, price, volume FROM trades ORDER BY created_at")
    trades = curs.fetchall()

    # Calculate positions using FIFO
    positions = {}
    for trade in trades:
        symbol, direction, price, volume = trade['symbol'], trade['direction'], trade['price'], trade['volume']
        if symbol not in positions:
            positions[symbol] = []
        if direction == '买入':
            positions[symbol].append({'price': price, 'volume': volume})
        else:
            remaining = volume
            lots = positions[symbol]
            i = 0
            while remaining > 0 and i < len(lots):
                used = min(lots[i]['volume'], remaining)
                lots[i]['volume'] -= used
                remaining -= used
                if lots[i]['volume'] == 0:
                    lots.pop(i)
                else:
                    i += 1

    # Calculate position details
    position_list = []
    for symbol, lots in positions.items():
        if not lots:
            continue
        total_vol = sum(l['volume'] for l in lots)
        avg_price = sum(l['price'] * l['volume'] for l in lots) / total_vol
        position_list.append({
            'symbol': symbol,
            'name': get_stock_name(symbol),
            'volume': total_vol,
            'avg_price': avg_price,
        })

    conn.close()
    return position_list, account


@router.get("", response_model=PortfolioSummary)
async def get_portfolio():
    """Get full portfolio summary."""
    position_list, account = calculate_positions_from_db()

    # Fetch real-time prices from Xueqiu
    symbols = [p['symbol'] for p in position_list]
    prices = get_realtime_prices(symbols) if symbols else {}

    total_position_value = 0
    positions = []
    for p in position_list:
        current_price = prices.get(p['symbol'], p['avg_price'])
        market_value = p['volume'] * current_price
        cost_value = p['volume'] * p['avg_price']
        floating_pnl = market_value - cost_value
        floating_pnl_pct = (current_price / p['avg_price'] - 1) * 100 if p['avg_price'] > 0 else 0
        total_position_value += market_value

        positions.append(PositionResponse(
            symbol=p['symbol'],
            name=p['name'],
            volume=p['volume'],
            avg_price=p['avg_price'],
            current_price=current_price,
            market_value=market_value,
            floating_pnl=floating_pnl,
            floating_pnl_pct=floating_pnl_pct,
            entry_date="",
        ))

    available_cash = account.get('available_cash', 0)
    initial_capital = account.get('initial_capital', 1000000)
    total_asset = available_cash + total_position_value
    total_float_pnl = sum(p.floating_pnl for p in positions)

    # Calculate realized PnL from trades
    import sqlite3
    db_file = settings.data_dir / "trades.db"
    realized_pnl = 0
    try:
        conn = sqlite3.connect(str(db_file), timeout=5)
        curs = conn.cursor()
        curs.execute("SELECT SUM(profit) FROM trades WHERE direction='卖出'")
        row = curs.fetchone()
        if row and row[0]:
            realized_pnl = row[0]
        conn.close()
    except Exception:
        pass

    account_response = AccountResponse(
        initial_capital=initial_capital,
        available_cash=available_cash,
        frozen_cash=account.get('frozen_cash', 0),
        position_value=total_position_value,
        total_asset=total_asset,
        realized_pnl=realized_pnl,
        float_pnl=total_float_pnl,
        total_pnl=total_float_pnl + realized_pnl,
        position_ratio=total_position_value / initial_capital * 100 if initial_capital > 0 else 0,
        positions=positions,
        updated_at=datetime.now(),
    )

    return PortfolioSummary(
        account=account_response,
        total_return=total_asset - initial_capital,
        total_return_pct=(total_asset / initial_capital - 1) * 100 if initial_capital > 0 else 0,
        win_rate=0,  # TODO: calculate from trades
    )


@router.get("/positions", response_model=list[PositionResponse])
async def get_positions():
    """Get current positions only."""
    position_list, _ = calculate_positions_from_db()
    symbols = [p['symbol'] for p in position_list]
    prices = get_realtime_prices(symbols) if symbols else {}

    positions = []
    for p in position_list:
        current_price = prices.get(p['symbol'], p['avg_price'])
        market_value = p['volume'] * current_price
        cost_value = p['volume'] * p['avg_price']
        floating_pnl = market_value - cost_value
        floating_pnl_pct = (current_price / p['avg_price'] - 1) * 100 if p['avg_price'] > 0 else 0

        positions.append(PositionResponse(
            symbol=p['symbol'],
            name=p['name'],
            volume=p['volume'],
            avg_price=p['avg_price'],
            current_price=current_price,
            market_value=market_value,
            floating_pnl=floating_pnl,
            floating_pnl_pct=floating_pnl_pct,
            entry_date="",
        ))
    return positions
