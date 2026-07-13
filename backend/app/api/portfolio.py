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
from app.models.account import AccountResponse, PositionResponse, PortfolioSummary, EquityPoint

settings = get_settings()

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


# Stock name cache
_stock_name_cache = {}
# Stock price cache (short TTL)
_stock_price_cache = {}
_price_cache_time = 0

# 费率常量（与 paper_engine.py 保持一致）
_BUY_COMMISSION = 0.0005
_SELL_FEE_RATE = 0.0015  # 佣金 0.05% + 印花税 0.1%


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
    """Fetch real-time stock prices and change_pct from Xueqiu (non-blocking).
    
    Returns: dict like {symbol: {"price": float, "change_pct": float}, ...}
    """
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
                    return symbol, {
                        "price": quote.get('current'),
                        "change_pct": quote.get('percent', 0) or 0,
                    }
            except Exception:
                pass
            return symbol, None

        # Use thread pool with 5s timeout to avoid blocking
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(_fetch_one, s): s for s in symbols}
            for future in concurrent.futures.as_completed(futures, timeout=5):
                try:
                    symbol, data = future.result(timeout=3)
                    if data is not None:
                        _stock_price_cache[symbol] = data
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
    """Calculate current positions and available_cash from trades.db using FIFO replay.

    不再依赖 account_info.available_cash（可能因引擎异常而偏离），
    完全从交易记录重放资金流水，确保 total_asset 与持仓自洽。

    Returns:
        (position_list, account, realized_pnl, win_rate)
    """
    import sqlite3

    db_file = settings.data_dir / "trades.db"
    if not db_file.exists():
        return [], {"available_cash": 0, "initial_capital": 1000000, "frozen_cash": 0}, 0, 0

    conn = sqlite3.connect(str(db_file), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    curs = conn.cursor()

    # 只读取 initial_capital 和 frozen_cash（initial 不变，frozen 由 orders 表决定）
    curs.execute("SELECT initial_capital, frozen_cash FROM account_info WHERE id=1")
    account_row = curs.fetchone()
    if not account_row:
        initial_cap = 1000000.0
        frozen_cash = 0.0
    else:
        initial_cap = account_row["initial_capital"]
        frozen_cash = account_row["frozen_cash"] or 0.0

    # 获取全部成交，排序策略与 paper_engine 一致
    curs.execute("""
        SELECT id, symbol, direction, price, volume
        FROM trades
        WHERE voided = 0 OR voided IS NULL
        ORDER BY COALESCE(trade_date, DATE(created_at)), id
    """)
    trades = curs.fetchall()

    # ── 同时查询 realized_pnl 和 win_rate（复用连接）──
    curs.execute("SELECT SUM(profit) FROM trades WHERE direction='卖出'")
    row = curs.fetchone()
    realized_pnl = float(row[0]) if row and row[0] else 0.0

    curs.execute("SELECT COUNT(*) as total, SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as wins FROM trades WHERE direction='卖出'")
    row = curs.fetchone()
    if row and row["total"] > 0:
        win_rate = round(row["wins"] / row["total"] * 100, 1)
    else:
        win_rate = 0.0

    conn.close()

    # ── FIFO 重放：同时计算持仓和资金 ──
    available_cash = initial_cap
    positions = {}

    for trade in trades:
        symbol = trade['symbol']
        direction = trade['direction']
        price = trade['price']
        volume = trade['volume']

        if direction == '买入':
            cost = price * volume * (1 + _BUY_COMMISSION)
            available_cash -= cost
            positions.setdefault(symbol, []).append({'price': price, 'volume': volume})

        elif direction == '卖出':
            lots = positions.get(symbol, [])
            if not lots:
                continue

            gross = price * volume
            sell_fee = gross * _SELL_FEE_RATE
            available_cash += gross - sell_fee

            # FIFO 出库
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

    # ── 构建持仓列表 ──
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

    account = {
        "initial_capital": initial_cap,
        "available_cash": available_cash,
        "frozen_cash": frozen_cash,
    }
    return position_list, account, realized_pnl, win_rate


def save_daily_snapshot(target_date: str = None) -> dict:
    """Compute and persist a daily portfolio snapshot to trades.db.

    Uses FIFO trade replay to determine positions up to target_date,
    values positions at real-time market prices for today, or at cost for historical dates.

    Returns: dict with success, trade_date, total_asset, price_source, etc.
    """
    if target_date is None:
        target_date = datetime.now().strftime('%Y-%m-%d')

    db_file = settings.data_dir / "trades.db"
    if not db_file.exists():
        return {'success': False, 'error': 'trades.db not found'}

    conn = sqlite3.connect(str(db_file), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    curs = conn.cursor()

    # Ensure table exists (idempotent)
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

    # Read initial_capital and frozen_cash
    curs.execute("SELECT initial_capital, frozen_cash FROM account_info WHERE id=1")
    row = curs.fetchone()
    if not row:
        conn.close()
        return {'success': False, 'error': 'No account_info found'}
    initial_cap = row['initial_capital']
    frozen_cash = row['frozen_cash'] or 0.0

    # Read all trades up to target_date
    curs.execute("""
        SELECT id, symbol, direction, price, volume, trade_date, created_at
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
    realized_pnl = float(curs.fetchone()[0])

    # FIFO replay to compute positions and available_cash
    available_cash = initial_cap
    positions_lots = {}

    for t in trades:
        sym = t['symbol']
        direction = t['direction']
        price = t['price']
        volume = t['volume']

        if direction == '买入':
            cost = price * volume * (1 + _BUY_COMMISSION)
            available_cash -= cost
            positions_lots.setdefault(sym, []).append({'price': price, 'volume': volume})
        elif direction == '卖出':
            lots = positions_lots.get(sym, [])
            if not lots:
                continue
            gross = price * volume
            sell_fee = gross * _SELL_FEE_RATE
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

    # Build position list for valuation
    position_list = []
    for sym, lots in positions_lots.items():
        if not lots:
            continue
        total_vol = sum(l['volume'] for l in lots)
        avg_price = sum(l['price'] * l['volume'] for l in lots) / total_vol
        position_list.append({'symbol': sym, 'volume': total_vol, 'avg_price': avg_price})

    # Determine valuation: market prices for today, cost for historical
    today_str = datetime.now().strftime('%Y-%m-%d')
    is_today = (target_date == today_str)
    price_source = 'cost'

    if is_today and position_list:
        symbols = [p['symbol'] for p in position_list]
        prices = get_realtime_prices(symbols)
        position_value = 0.0
        for p in position_list:
            price_data = prices.get(p['symbol'], {})
            if isinstance(price_data, dict):
                market_price = price_data.get('price', p['avg_price'])
            else:
                market_price = p['avg_price']
            position_value += market_price * p['volume']
        if prices:
            price_source = 'market'
    else:
        position_value = sum(p['avg_price'] * p['volume'] for p in position_list)

    cost_value = sum(p['avg_price'] * p['volume'] for p in position_list)
    total_asset = available_cash + frozen_cash + position_value
    float_pnl = position_value - cost_value
    total_pnl = total_asset - initial_cap

    curs.execute('''
        INSERT OR REPLACE INTO daily_snapshot
        (trade_date, total_asset, available_cash, frozen_cash, position_value,
         cost_value, realized_pnl, float_pnl, total_pnl, initial_capital, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (target_date, total_asset, available_cash, frozen_cash, position_value,
          cost_value, realized_pnl, float_pnl, total_pnl, initial_cap, datetime.now().isoformat()))

    conn.commit()
    conn.close()

    return {
        'success': True,
        'trade_date': target_date,
        'total_asset': round(total_asset, 2),
        'available_cash': round(available_cash, 2),
        'frozen_cash': round(frozen_cash, 2),
        'position_value': round(position_value, 2),
        'cost_value': round(cost_value, 2),
        'realized_pnl': round(realized_pnl, 2),
        'float_pnl': round(float_pnl, 2),
        'total_pnl': round(total_pnl, 2),
        'initial_capital': initial_cap,
        'price_source': price_source,
        'position_count': len(position_list),
    }


@router.get("", response_model=PortfolioSummary)
async def get_portfolio():
    """Get full portfolio summary."""
    position_list, account, realized_pnl, win_rate = calculate_positions_from_db()

    # Fetch real-time prices from Xueqiu
    symbols = [p['symbol'] for p in position_list]
    prices = get_realtime_prices(symbols) if symbols else {}

    # 获取 High Water Mark 数据（牛股计算器策略）
    high_water_marks = {}
    try:
        from core.utils.strategy_chain import StrategyChain
        chain = StrategyChain()
        for p in position_list:
            hwm = chain.get_high_water_mark(p['symbol'])
            if hwm:
                high_water_marks[p['symbol']] = hwm
            # 同时更新 high water mark
            price_data = prices.get(p['symbol'], {})
            current_p = price_data.get('price', p['avg_price']) if isinstance(price_data, dict) else price_data
            if current_p > 0:
                chain.update_high_water_mark(p['symbol'], current_p)
    except Exception:
        pass

    total_position_value = 0
    positions = []
    for p in position_list:
        price_data = prices.get(p['symbol'], {})
        if isinstance(price_data, dict):
            current_price = price_data.get('price', p['avg_price'])
            change_pct = price_data.get('change_pct', 0)
        else:
            # backward compatibility with old cache format
            current_price = price_data
            change_pct = 0
        market_value = p['volume'] * current_price
        cost_value = p['volume'] * p['avg_price']
        floating_pnl = market_value - cost_value
        floating_pnl_pct = (current_price / p['avg_price'] - 1) * 100 if p['avg_price'] > 0 else 0
        total_position_value += market_value

        # 今日盈亏 = volume * (current_price - prev_close)
        # prev_close = current_price / (1 + change_pct / 100)
        if abs(100 + change_pct) > 0.001:
            prev_close = current_price / (1 + change_pct / 100)
        else:
            prev_close = current_price
        today_pnl = p['volume'] * (current_price - prev_close)

        # 附加 High Water Mark
        hwm = high_water_marks.get(p['symbol'], {})

        positions.append(PositionResponse(
            symbol=p['symbol'],
            name=p['name'],
            volume=p['volume'],
            avg_price=p['avg_price'],
            current_price=current_price,
            change_pct=change_pct,
            today_pnl=today_pnl,
            market_value=market_value,
            floating_pnl=floating_pnl,
            floating_pnl_pct=floating_pnl_pct,
            entry_date="",
            high_water_mark=hwm.get('high_price'),
            high_water_date=hwm.get('high_date'),
            days_since_high=hwm.get('days_since_high'),
        ))

    available_cash = account.get('available_cash', 0)
    initial_capital = account.get('initial_capital', 1000000)
    total_asset = available_cash + account.get('frozen_cash', 0) + total_position_value
    total_float_pnl = sum(p.floating_pnl for p in positions)

    # 🔧 total_pnl = total_asset - initial_capital（始终与 total_return 一致）
    #     float_pnl 作为推导值 = total_pnl - realized_pnl，保证三数自洽
    total_pnl = total_asset - initial_capital
    derived_float_pnl = total_pnl - realized_pnl

    account_response = AccountResponse(
        initial_capital=initial_capital,
        available_cash=available_cash,
        frozen_cash=account.get('frozen_cash', 0),
        position_value=total_position_value,
        total_asset=total_asset,
        realized_pnl=realized_pnl,
        float_pnl=derived_float_pnl,
        total_pnl=total_pnl,
        position_ratio=total_position_value / initial_capital * 100 if initial_capital > 0 else 0,
        positions=positions,
        updated_at=datetime.now(),
    )

    return PortfolioSummary(
        account=account_response,
        total_return=total_pnl,  # 与 account.total_pnl 同源，始终一致
        total_return_pct=(total_asset / initial_capital - 1) * 100 if initial_capital > 0 else 0,
        win_rate=win_rate,
    )


@router.get("/positions", response_model=list[PositionResponse])
async def get_positions():
    """Get current positions only."""
    position_list, _ = calculate_positions_from_db()[:2]
    symbols = [p['symbol'] for p in position_list]
    prices = get_realtime_prices(symbols) if symbols else {}

    positions = []
    for p in position_list:
        price_data = prices.get(p['symbol'], {})
        if isinstance(price_data, dict):
            current_price = price_data.get('price', p['avg_price'])
            change_pct = price_data.get('change_pct', 0)
        else:
            current_price = price_data
            change_pct = 0
        market_value = p['volume'] * current_price
        cost_value = p['volume'] * p['avg_price']
        floating_pnl = market_value - cost_value
        floating_pnl_pct = (current_price / p['avg_price'] - 1) * 100 if p['avg_price'] > 0 else 0

        # 获取 High Water Mark
        hwm = {}
        try:
            from core.utils.strategy_chain import StrategyChain
            chain = StrategyChain()
            hwm_data = chain.get_high_water_mark(p['symbol'])
            if hwm_data:
                hwm = hwm_data
            # 更新 high water mark
            if current_price > 0:
                chain.update_high_water_mark(p['symbol'], current_price)
        except Exception:
            pass

        positions.append(PositionResponse(
            symbol=p['symbol'],
            name=p['name'],
            volume=p['volume'],
            avg_price=p['avg_price'],
            current_price=current_price,
            change_pct=change_pct,
            market_value=market_value,
            floating_pnl=floating_pnl,
            floating_pnl_pct=floating_pnl_pct,
            entry_date="",
            high_water_mark=hwm.get('high_price'),
            high_water_date=hwm.get('high_date'),
            days_since_high=hwm.get('days_since_high'),
        ))
    return positions


@router.post("/unfreeze")
async def unfreeze_funds():
    """Manually unfreeze all frozen funds.
    
    Used when trading exceptions cause funds to be incorrectly frozen.
    Moves all frozen_cash back to available_cash and cancels any stuck orders.
    """
    db_file = settings.data_dir / "trades.db"
    if not db_file.exists():
        raise HTTPException(status_code=404, detail="交易数据库不存在")
    
    try:
        conn = sqlite3.connect(str(db_file), timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        curs = conn.cursor()
        
        # Read current account
        curs.execute("SELECT * FROM account_info WHERE id=1")
        account = curs.fetchone()
        if not account:
            conn.close()
            raise HTTPException(status_code=404, detail="账户信息不存在")
        
        frozen = account['frozen_cash']
        available = account['available_cash']
        
        if frozen <= 0:
            conn.close()
            return {
                "success": True,
                "message": "没有冻结资金需要解冻",
                "unfrozen_amount": 0,
                "available_cash": available,
                "frozen_cash": 0,
                "orders_cancelled": 0,
            }
        
        # Cancel any stuck orders (status = 'submitting' or 'submitted')
        curs.execute(
            "SELECT COUNT(*) as cnt FROM orders WHERE status IN ('submitting', 'submitted')"
        )
        stuck_count = curs.fetchone()['cnt']
        
        if stuck_count > 0:
            curs.execute(
                "UPDATE orders SET status='cancelled', cancelled_at=datetime('now', 'localtime') "
                "WHERE status IN ('submitting', 'submitted')"
            )
        
        # Unfreeze funds
        new_available = available + frozen
        curs.execute(
            "UPDATE account_info SET available_cash=?, frozen_cash=0, updated_at=? WHERE id=1",
            (new_available, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "message": f"已解冻 ¥{frozen:,.2f}，取消 {stuck_count} 笔卡住订单",
            "unfrozen_amount": frozen,
            "available_cash": new_available,
            "frozen_cash": 0,
            "orders_cancelled": stuck_count,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解冻失败: {str(e)}")


@router.post("/daily-snapshot")
async def trigger_daily_snapshot(date: str = Query(None, description="Target date YYYY-MM-DD, defaults to today")):
    """Manually trigger a daily portfolio snapshot.

    Computes current positions and total_asset (valued at market prices for today,
    at cost for historical dates) and persists to trades.db::daily_snapshot.
    """
    result = save_daily_snapshot(target_date=date)
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', 'Snapshot failed'))
    return result


@router.get("/equity-history", response_model=list[EquityPoint])
async def get_equity_history(days: int = Query(60, ge=1, le=365)):
    """
    Get daily equity curve = available_cash + position_value on each day.

    历史日使用持仓成本价估值，当日使用实时市价估值，
    确保权益曲线与 total_asset 一致。
    """
    from datetime import datetime as dt, timedelta

    db_file = settings.data_dir / "trades.db"
    if not db_file.exists():
        return []

    conn = sqlite3.connect(str(db_file), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    curs = conn.cursor()

    curs.execute("CREATE INDEX IF NOT EXISTS idx_trades_dir_date ON trades(direction, created_at)")

    # Get initial capital
    curs.execute("SELECT initial_capital FROM account_info WHERE id=1")
    row = curs.fetchone()
    initial_capital = row["initial_capital"] if row else 1000000.0

    # 获取全部成交，按 trade_date, id 排序（与 FIFO 策略一致）
    curs.execute("""
        SELECT id, symbol, direction, price, volume, trade_date, created_at
        FROM trades
        WHERE voided = 0 OR voided IS NULL
        ORDER BY trade_date, id
    """)
    all_trades = curs.fetchall()

    # 按日期分组 trade（使用 trade_date 作为日期）
    trades_by_date = {}
    for t in all_trades:
        # trade_date 可能为 NULL（旧数据），回退到 created_at 的日期部分
        td = t["trade_date"] or (t["created_at"][:10] if t["created_at"] else None)
        if td:
            trades_by_date.setdefault(td, []).append(t)

    if not trades_by_date:
        conn.close()
        return []

    # 找到最早 & 最晚日期
    sorted_dates = sorted(trades_by_date.keys())
    min_trade_date = dt.strptime(sorted_dates[0], "%Y-%m-%d")
    today = dt.now()
    start_date = today - timedelta(days=days + 5)
    if start_date < min_trade_date:
        start_date = min_trade_date

    # 读取已落库的快照（市价估值优先）
    snapshots = {}
    try:
        curs.execute("SELECT trade_date, total_asset FROM daily_snapshot ORDER BY trade_date")
        for row in curs.fetchall():
            snapshots[row['trade_date']] = row['total_asset']
    except sqlite3.OperationalError:
        pass  # 表还不存在，全部回退到成本价重放

    # 获取当前持仓的实时价格（仅用于最后一天）
    today_str = today.strftime("%Y-%m-%d")
    current_positions, _account, _realized, _winrate = calculate_positions_from_db()
    symbols = [p['symbol'] for p in current_positions]
    realtime_prices = get_realtime_prices(symbols) if symbols else {}

    # ── 逐日重放交易，计算每日权益 ──
    available_cash = initial_capital
    positions = {}  # symbol -> [{'price': float, 'volume': int}, ...]

    # 先处理 start_date 之前的所有 trade
    for d in sorted_dates:
        if d >= start_date.strftime("%Y-%m-%d"):
            break
        for t in trades_by_date.get(d, []):
            available_cash, positions = _apply_trade(t, available_cash, positions)

    # 生成每日权益曲线
    result = []
    current = start_date
    while current <= today and len(result) < days:
        date_str = current.strftime("%Y-%m-%d")

        # 应用当日的交易
        for t in trades_by_date.get(date_str, []):
            available_cash, positions = _apply_trade(t, available_cash, positions)

        # 计算当日持仓市值
        if date_str in snapshots:
            # 已落库的快照：直接使用市价估值
            equity = snapshots[date_str]
        else:
            if date_str == today_str:
                # 当日：使用实时市价
                position_value = 0.0
                for sym, lots in positions.items():
                    total_vol = sum(l['volume'] for l in lots)
                    if total_vol > 0:
                        price_data = realtime_prices.get(sym, {})
                        if isinstance(price_data, dict):
                            price = price_data.get('price')
                        else:
                            price = price_data if isinstance(price_data, (int, float)) else None
                        if not price:
                            # 实时价不可用时回退到成本价
                            price = sum(l['price'] * l['volume'] for l in lots) / total_vol
                        position_value += price * total_vol
            else:
                # 历史日：使用持仓成本价估值
                position_value = sum(
                    l['price'] * l['volume']
                    for lots in positions.values()
                    for l in lots
                )
            equity = available_cash + position_value
        result.append(EquityPoint(date=date_str, equity=round(equity, 2)))

        current += timedelta(days=1)

    conn.close()

    # Limit to most recent N days
    if len(result) > days:
        result = result[-days:]

    return result




def _apply_trade(trade, cash: float, positions: dict) -> tuple:
    """将一笔成交应用到账户状态，返回 (new_cash, new_positions)"""
    symbol = trade["symbol"]
    direction = trade["direction"]
    price = trade["price"]
    volume = trade["volume"]

    if direction == '买入':
        cost = price * volume * (1 + _BUY_COMMISSION)
        cash -= cost
        positions.setdefault(symbol, []).append({'price': price, 'volume': volume})

    elif direction == '卖出':
        lots = positions.get(symbol, [])
        gross = price * volume
        sell_fee = gross * _SELL_FEE_RATE
        cash += gross - sell_fee

        # FIFO 出库
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

    return cash, positions
