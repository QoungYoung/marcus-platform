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

        # 附加 High Water Mark
        hwm = high_water_marks.get(p['symbol'], {})

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

    available_cash = account.get('available_cash', 0)
    initial_capital = account.get('initial_capital', 1000000)
    total_asset = available_cash + account.get('frozen_cash', 0) + total_position_value
    total_float_pnl = sum(p.floating_pnl for p in positions)

    # Calculate realized PnL from trades
    import sqlite3
    db_file = settings.data_dir / "trades.db"
    realized_pnl = 0
    win_rate = 0
    try:
        conn = sqlite3.connect(str(db_file), timeout=5)
        curs = conn.cursor()
        curs.execute("SELECT SUM(profit) FROM trades WHERE direction='卖出'")
        row = curs.fetchone()
        if row and row[0]:
            realized_pnl = row[0]
        # 计算胜率：盈利卖单 / 总卖单
        curs.execute("SELECT COUNT(*) as total, SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as wins FROM trades WHERE direction='卖出'")
        row = curs.fetchone()
        if row and row[0] > 0:
            win_rate = round(row[1] / row[0] * 100, 1) if row[0] > 0 else 0
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

    # ── 计算板块集中度 ──
    sector_concentration = None
    if positions:
        try:
            import sqlite3
            pool_db = settings.data_dir / "stock_pool.db"
            if pool_db.exists():
                conn = sqlite3.connect(str(pool_db))
                curs = conn.cursor()

                # 按申万三级行业分组统计持仓市值
                industry_exposure = {}
                for p in positions:
                    bare_code = p.symbol[2:] if len(p.symbol) > 4 else p.symbol
                    curs.execute(
                        "SELECT industry FROM stock_pool WHERE symbol = ? OR ts_code = ?",
                        (bare_code, p.symbol)
                    )
                    row = curs.fetchone()
                    if row and row[0]:
                        ind = row[0] or "未知行业"
                    else:
                        ind = "未知行业"
                    industry_exposure[ind] = industry_exposure.get(ind, 0) + p.market_value

                conn.close()

                max_industry = max(industry_exposure, key=industry_exposure.get) if industry_exposure else ""
                max_concentration = (
                    round(industry_exposure[max_industry] / total_asset * 100, 1)
                    if total_asset > 0 and max_industry else 0
                )

                sector_concentration = {
                    "max_sector": max_industry,
                    "concentration_pct": max_concentration,
                    "breakdown": {k: round(v / total_asset * 100, 1) if total_asset > 0 else 0
                                 for k, v in industry_exposure.items()},
                }
        except Exception:
            pass

    # ── 计算持仓弱势排名 ──
    if positions:
        try:
            import sqlite3
            pool_db = settings.data_dir / "stock_pool.db"
            if pool_db.exists():
                conn = sqlite3.connect(str(pool_db))
                curs = conn.cursor()

                for p in positions:
                    bare_code = p.symbol[2:] if len(p.symbol) > 4 else p.symbol
                    # 查找股票所属的概念板块
                    curs.execute(
                        "SELECT concept_name FROM stock_concept_map WHERE ts_code LIKE ? LIMIT 1",
                        (f"%{bare_code}%",)
                    )
                    row = curs.fetchone()
                    if row:
                        concept = row[0]
                        # 获取该概念板块所有成分股
                        curs.execute(
                            "SELECT ts_code FROM stock_concept_map WHERE concept_name = ?",
                            (concept,)
                        )
                        members = [r[0] for r in curs.fetchall()]
                        if len(members) > 1:
                            # 获取同板块股票的实时涨幅并排序
                            member_changes = []
                            for m_ts_code in members[:30]:  # 限制查询数量
                                m_symbol = m_ts_code.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
                                # 构建Xueqiu符号
                                if ".SH" in m_ts_code:
                                    m_xq = f"SH{m_symbol}"
                                elif ".SZ" in m_ts_code:
                                    m_xq = f"SZ{m_symbol}"
                                else:
                                    m_xq = f"BJ{m_symbol}"
                                try:
                                    m_price = get_realtime_prices([m_xq]).get(m_xq, {})
                                    if isinstance(m_price, dict):
                                        m_change = m_price.get("change_pct", 0) or 0
                                    else:
                                        m_change = 0
                                    member_changes.append((m_xq, abs(float(m_change))))
                                except (ValueError, TypeError):
                                    member_changes.append((m_xq, 0))

                            member_changes.sort(key=lambda x: x[1], reverse=True)
                            # 找到当前持仓的排名
                            own_rank = None
                            for rank, (mc_sym, _) in enumerate(member_changes, 1):
                                if bare_code in mc_sym.upper():
                                    own_rank = rank
                                    break

                            if own_rank:
                                p.sector_rank = own_rank
                                p.sector_rank_pct = round(own_rank / len(member_changes) * 100, 1) if member_changes else None

                conn.close()
        except Exception:
            pass

    return PortfolioSummary(
        account=account_response,
        total_return=total_asset - initial_capital,
        total_return_pct=(total_asset / initial_capital - 1) * 100 if initial_capital > 0 else 0,
        win_rate=win_rate,
        sector_concentration=sector_concentration,
    )


@router.get("/positions", response_model=list[PositionResponse])
async def get_positions():
    """Get current positions only."""
    position_list, _ = calculate_positions_from_db()
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


@router.get("/equity-history", response_model=list[EquityPoint])
async def get_equity_history(days: int = Query(60, ge=1, le=365)):
    """
    Get daily equity curve aggregated from realized trade P&L.
    Equity on each day = initial_capital + cumulative realized profit up to that day.
    """
    from datetime import datetime as dt, timedelta

    db_file = settings.data_dir / "trades.db"
    if not db_file.exists():
        return []

    conn = sqlite3.connect(str(db_file), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")  # 快速失败而非长时间等待写锁
    curs = conn.cursor()

    # 索引已在 lifespan 预热阶段创建，此处仅作兜底（幂等，极快）
    curs.execute("CREATE INDEX IF NOT EXISTS idx_trades_dir_date ON trades(direction, created_at)")

    # Get initial capital
    curs.execute("SELECT initial_capital FROM account_info WHERE id=1")
    row = curs.fetchone()
    initial_capital = row["initial_capital"] if row else 1000000.0

    # 只查询日期范围内的卖出记录（避免全表扫描）
    start_date = (dt.now() - timedelta(days=days + 10)).strftime("%Y-%m-%d")

    # 先查窗口之前的累计利润
    curs.execute("""
        SELECT COALESCE(SUM(profit), 0)
        FROM trades
        WHERE direction = '卖出' AND profit IS NOT NULL AND DATE(created_at) < ?
    """, (start_date,))
    prior_profit = curs.fetchone()[0] or 0

    # 再查窗口内的每日利润
    curs.execute("""
        SELECT DATE(created_at) as trade_date, SUM(profit) as daily_profit
        FROM trades
        WHERE direction = '卖出' AND profit IS NOT NULL AND DATE(created_at) >= ?
        GROUP BY DATE(created_at)
        ORDER BY trade_date
    """, (start_date,))
    rows = curs.fetchall()
    conn.close()

    # Build daily profit map
    daily_profit = {}
    for row in rows:
        daily_profit[row["trade_date"]] = row["daily_profit"] or 0

    # Generate equity curve for the requested window
    today = dt.now()
    equity = initial_capital + prior_profit
    result = []
    current = dt.strptime(start_date, "%Y-%m-%d")
    while current <= today and len(result) < days:
        date_str = current.strftime("%Y-%m-%d")
        if date_str in daily_profit:
            equity += daily_profit[date_str]
        result.append(EquityPoint(date=date_str, equity=round(equity, 2)))
        current += timedelta(days=1)

    # Limit to most recent N days
    if len(result) > days:
        result = result[-days:]

    return result
