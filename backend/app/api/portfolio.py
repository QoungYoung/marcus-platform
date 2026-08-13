# -*- coding: utf-8 -*-
"""
Portfolio API endpoints.
"""
import math
import sys
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi import Query
from sqlalchemy import func

from app.config import get_settings
from app.database import SessionLocal
from app.models.account import AccountResponse, PositionResponse, PortfolioSummary, EquityPoint, DailyPnlBreakdown, DailyStockPnl, CapitalAdjustRequest
from app.models.paper_trade import PaperAccountInfo, PaperTrade, PaperDailySnapshot, PaperOrder, PaperCapitalAdjustment

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
    """Get stock name from symbol, query PostgreSQL stock_pool table."""
    if symbol in _stock_name_cache:
        return _stock_name_cache[symbol]

    # Try to get from PostgreSQL stock_pool table
    try:
        from app.services.market_reference import get_stock_name as _pg_get_stock_name
        name = _pg_get_stock_name(symbol)
        if name:
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
                        "last_close": quote.get('last_close', 0) or 0,
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


def calculate_positions_from_db(account: str = "stock"):
    """Calculate current positions from PostgreSQL paper_trades using FIFO replay.

    available_cash 直接从 paper_account_info 读取（PostgreSQL FOR UPDATE 行锁保证一致性）。

    Args:
        account: 账户标识（默认 stock）

    Returns:
        (position_list, account, realized_pnl, win_rate)
    """
    db = SessionLocal()
    try:
        acct = db.query(PaperAccountInfo).filter(PaperAccountInfo.account_id == account).first()
        if not acct:
            return [], {"available_cash": 0, "initial_capital": 1000000, "frozen_cash": 0}, 0, 0

        initial_cap = float(acct.initial_capital)
        available_cash = float(acct.available_cash)
        frozen_cash = float(acct.frozen_cash or 0)

        trades = db.query(PaperTrade).filter(
            PaperTrade.account_id == account,
            (PaperTrade.voided == 0) | (PaperTrade.voided == None)
        ).order_by(
            func.coalesce(PaperTrade.trade_date, func.substr(PaperTrade.created_at, 1, 10)),
            PaperTrade.id
        ).all()

        realized_pnl = float(
            db.query(func.coalesce(func.sum(PaperTrade.profit), 0)).filter(
                PaperTrade.account_id == account,
                PaperTrade.direction == '卖出',
                (PaperTrade.voided == 0) | (PaperTrade.voided == None)
            ).scalar() or 0
        )

        total_sells = db.query(func.count()).filter(
            PaperTrade.account_id == account,
            PaperTrade.direction == '卖出',
            (PaperTrade.voided == 0) | (PaperTrade.voided == None)
        ).scalar() or 0
        wins = db.query(func.count()).filter(
            PaperTrade.account_id == account,
            PaperTrade.direction == '卖出',
            PaperTrade.profit > 0,
            (PaperTrade.voided == 0) | (PaperTrade.voided == None)
        ).scalar() or 0
        win_rate = round(wins / total_sells * 100, 1) if total_sells > 0 else 0.0
    finally:
        db.close()

    # ── FIFO 重放：仅计算持仓（资金已从 paper_account_info 直接读取） ──
    positions = {}
    for trade in trades:
        symbol = trade.symbol
        direction = trade.direction
        price = trade.price
        volume = trade.volume

        if direction == '买入':
            entry_date = trade.trade_date or (trade.created_at[:10] if trade.created_at else '')
            positions.setdefault(symbol, []).append({'price': price, 'volume': volume, 'entry_date': entry_date})
        elif direction == '卖出':
            lots = positions.get(symbol, [])
            if not lots:
                continue
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
        entry_dates = [l['entry_date'] for l in lots if l.get('entry_date')]
        entry_date = min(entry_dates) if entry_dates else ''
        position_list.append({
            'symbol': symbol,
            'name': get_stock_name(symbol),
            'volume': total_vol,
            'avg_price': avg_price,
            'entry_date': entry_date,
        })

    account = {
        "initial_capital": initial_cap,
        "available_cash": available_cash,
        "frozen_cash": frozen_cash,
    }
    return position_list, account, realized_pnl, win_rate


def _calc_week_pnl(positions: list) -> tuple:
    """计算本周持仓盈亏：(本周已实现, 本周浮盈)

    本周已实现：本周一至今日所有卖出交易的 profit 之和
    本周浮盈：本周内有买入记录的当前持仓的浮盈之和
    """
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    monday_str = monday.strftime('%Y-%m-%d')

    db = SessionLocal()
    try:
        week_realized = float(
            db.query(func.coalesce(func.sum(PaperTrade.profit), 0)).filter(
                PaperTrade.direction == '卖出',
                (PaperTrade.voided == 0) | (PaperTrade.voided == None),
                func.substr(PaperTrade.created_at, 1, 10) >= monday_str
            ).scalar() or 0
        )

        week_bought = {
            row[0] for row in
            db.query(PaperTrade.symbol).filter(
                PaperTrade.direction == '买入',
                (PaperTrade.voided == 0) | (PaperTrade.voided == None),
                func.substr(PaperTrade.created_at, 1, 10) >= monday_str
            ).distinct().all()
        }
    finally:
        db.close()

    week_float = sum(p.floating_pnl for p in positions if p.symbol in week_bought)
    return week_realized, week_float


def _normalize_to_ts_code(symbol: str) -> str:
    """Marcus 代码 SH600900 → Tushare ts_code 600900.SH"""
    code = symbol[2:] if len(symbol) > 4 and symbol[:2] in ('SH', 'SZ', 'BJ') else symbol
    if symbol.startswith("SH") or code.startswith("6"):
        return f"{code}.SH"
    elif symbol.startswith("SZ") or code.startswith(("0", "3")):
        return f"{code}.SZ"
    elif symbol.startswith("BJ") or code.startswith(("8", "4")):
        return f"{code}.BJ"
    return f"{code}.SH"


def _get_tushare_close_prices(symbols: list, trade_date: str) -> dict:
    """从 Tushare 获取指定日期的收盘价。

    Returns: {marcus_symbol: close_price}
    """
    if not symbols:
        return {}
    try:
        from app.core.trading._api_config import get_tushare_pro
        pro = get_tushare_pro()
    except Exception:
        return {}

    ts_codes = [_normalize_to_ts_code(s) for s in symbols]
    code_to_marcus = {ts: ms for ts, ms in zip(ts_codes, symbols)}
    date_str = trade_date.replace("-", "")

    try:
        df = pro.daily(
            ts_code=",".join(ts_codes),
            trade_date=date_str,
            fields="ts_code,trade_date,close",
        )
    except Exception:
        return {}

    if df is None or df.empty:
        return {}

    result = {}
    for _, row in df.iterrows():
        marcus = code_to_marcus.get(row["ts_code"])
        if marcus:
            result[marcus] = float(row["close"])
    return result


def save_daily_snapshot(target_date: str = None, account: str = "stock") -> dict:
    """Compute and persist a daily portfolio snapshot to PostgreSQL paper_daily_snapshot.

    Uses FIFO trade replay to determine positions up to target_date,
    values positions at real-time market prices for today, or at cost for historical dates.

    Returns: dict with success, trade_date, total_asset, price_source, etc.
    """
    if target_date is None:
        target_date = datetime.now().strftime('%Y-%m-%d')

    db = SessionLocal()
    try:
        acct = db.query(PaperAccountInfo).filter(PaperAccountInfo.account_id == account).first()
        if not acct:
            return {'success': False, 'error': 'No account_info found'}
        initial_cap = float(acct.initial_capital)
        frozen_cash = float(acct.frozen_cash or 0)

        trades = db.query(PaperTrade).filter(
            PaperTrade.account_id == account,
            (PaperTrade.voided == 0) | (PaperTrade.voided == None),
            (PaperTrade.trade_date <= target_date) |
            ((PaperTrade.trade_date == None) & (func.substr(PaperTrade.created_at, 1, 10) <= target_date))
        ).order_by(
            func.coalesce(PaperTrade.trade_date, func.substr(PaperTrade.created_at, 1, 10)),
            PaperTrade.id
        ).all()

        realized_pnl = float(
            db.query(func.coalesce(func.sum(PaperTrade.profit), 0)).filter(
                PaperTrade.account_id == account,
                PaperTrade.direction == '卖出',
                (PaperTrade.voided == 0) | (PaperTrade.voided == None),
                (PaperTrade.trade_date <= target_date) |
                ((PaperTrade.trade_date == None) & (func.substr(PaperTrade.created_at, 1, 10) <= target_date))
            ).scalar() or 0
        )

        capital_adjustments = float(
            db.query(func.coalesce(func.sum(PaperCapitalAdjustment.amount), 0)).filter(
                PaperCapitalAdjustment.account_id == account,
                func.substr(PaperCapitalAdjustment.created_at, 1, 10) <= target_date
            ).scalar() or 0
        )
    finally:
        db.close()

    # ── FIFO 重放：计算截至 target_date 的持仓和资金 ──
    available_cash = initial_cap
    positions_lots = {}

    for t in trades:
        sym = t.symbol
        direction = t.direction
        price = t.price
        volume = t.volume

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

    # 手动资金调整（入金/出金）叠加到回放现金
    available_cash += capital_adjustments

    position_list = []
    for sym, lots in positions_lots.items():
        if not lots:
            continue
        total_vol = sum(l['volume'] for l in lots)
        avg_price = sum(l['price'] * l['volume'] for l in lots) / total_vol
        position_list.append({'symbol': sym, 'volume': total_vol, 'avg_price': avg_price})

    today_str = datetime.now().strftime('%Y-%m-%d')
    is_today = (target_date == today_str)
    price_source = 'cost'

    if position_list:
        if is_today:
            # 当日：实时行情
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
            # 历史日期：Tushare 收盘价
            close_prices = _get_tushare_close_prices(
                [p['symbol'] for p in position_list], target_date
            )
            position_value = 0.0
            priced = 0
            for p in position_list:
                cp = close_prices.get(p['symbol'])
                if cp and cp > 0:
                    position_value += cp * p['volume']
                    priced += 1
                else:
                    position_value += p['avg_price'] * p['volume']
            if priced > 0:
                price_source = 'tushare'
    else:
        position_value = 0.0

    cost_value = sum(p['avg_price'] * p['volume'] for p in position_list)
    total_asset = available_cash + frozen_cash + position_value
    float_pnl = position_value - cost_value
    total_pnl = total_asset - initial_cap

    # ── Upsert into PostgreSQL ──
    db = SessionLocal()
    try:
        snap = db.query(PaperDailySnapshot).filter(
            PaperDailySnapshot.account_id == account,
            PaperDailySnapshot.trade_date == target_date,
        ).first()
        if snap:
            snap.total_asset = total_asset
            snap.available_cash = available_cash
            snap.frozen_cash = frozen_cash
            snap.position_value = position_value
            snap.cost_value = cost_value
            snap.realized_pnl = realized_pnl
            snap.float_pnl = float_pnl
            snap.total_pnl = total_pnl
            snap.initial_capital = initial_cap
            snap.created_at = datetime.now().isoformat()
        else:
            db.add(PaperDailySnapshot(
                account_id=account,
                trade_date=target_date,
                total_asset=total_asset,
                available_cash=available_cash,
                frozen_cash=frozen_cash,
                position_value=position_value,
                cost_value=cost_value,
                realized_pnl=realized_pnl,
                float_pnl=float_pnl,
                total_pnl=total_pnl,
                initial_capital=initial_cap,
                created_at=datetime.now().isoformat(),
            ))
        db.commit()
    finally:
        db.close()

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


def _compute_sector_concentration(positions: list, total_position_value: float) -> dict | None:
    """计算行业集中度（申万行业分类）。

    从 PostgreSQL stock_pool 表的 industry 查询每个持仓股的申万行业，
    聚合各行业的市值权重。每只股票只属于一个行业，无需均分。

    Returns: dict with sectors, max_sector, concentration_level
    """
    if not positions or total_position_value <= 0:
        return None

    try:
        from app.services.market_reference import get_stock_industry

        # 收集所有持仓股的申万行业
        industry_values: dict[str, float] = {}
        industry_stocks: dict[str, set] = {}

        for p in positions:
            symbol = p.symbol if isinstance(p, dict) else getattr(p, 'symbol', '')
            market_value = (
                p.market_value if hasattr(p, 'market_value')
                else p.get('market_value', 0) if isinstance(p, dict)
                else 0
            )

            industry = get_stock_industry(symbol) or "其他"
            industry_values[industry] = industry_values.get(industry, 0.0) + market_value
            industry_stocks.setdefault(industry, set()).add(symbol)

        if not industry_values:
            return None

        # 构建 sectors 列表
        sectors = sorted(
            [
                {
                    "name": name,
                    "weight_pct": round(val / total_position_value * 100, 1),
                    "stock_count": len(industry_stocks.get(name, set())),
                }
                for name, val in industry_values.items()
            ],
            key=lambda x: x["weight_pct"],
            reverse=True,
        )

        max_sector = sectors[0] if sectors else None
        max_weight = max_sector["weight_pct"] if max_sector else 0
        if max_weight > 50:
            level = "集中"
        elif max_weight > 30:
            level = "适中"
        else:
            level = "分散"

        return {
            "sectors": sectors,
            "max_sector": max_sector,
            "concentration_level": level,
        }
    except Exception as e:
        print(f"[Portfolio] sector_concentration 计算失败: {e}", flush=True)
        return None


@router.get("", response_model=PortfolioSummary)
async def get_portfolio(account: str = Query("stock", description="账户标识")):
    """Get full portfolio summary."""
    position_list, account_info, realized_pnl, win_rate = calculate_positions_from_db(account)

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
        # 优先使用API返回的昨收价, 开盘前current_price==last_close→today_pnl=0
        last_close = price_data.get('last_close', 0) if isinstance(price_data, dict) else 0
        if last_close > 0:
            prev_close = last_close
        elif abs(100 + change_pct) > 0.001:
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
            entry_date=p.get('entry_date', ''),
            high_water_mark=hwm.get('high_price'),
            high_water_date=hwm.get('high_date'),
            days_since_high=hwm.get('days_since_high'),
        ))

    available_cash = account_info.get('available_cash', 0)
    initial_capital = account_info.get('initial_capital', 1000000)
    total_asset = available_cash + account_info.get('frozen_cash', 0) + total_position_value
    total_float_pnl = sum(p.floating_pnl for p in positions)

    # ── 本周持仓盈亏 ──
    week_realized_pnl, week_float_pnl = _calc_week_pnl(positions)
    week_total = week_realized_pnl + week_float_pnl
    print(
        f"[Portfolio] 本周盈亏: 总{week_total:+.2f} "
        f"(已实现{week_realized_pnl:+.2f} / 浮盈{week_float_pnl:+.2f})",
        flush=True,
    )

    total_pnl = total_asset - initial_capital

    account_response = AccountResponse(
        initial_capital=initial_capital,
        available_cash=available_cash,
        frozen_cash=account_info.get('frozen_cash', 0),
        position_value=total_position_value,
        total_asset=total_asset,
        realized_pnl=realized_pnl,
        float_pnl=total_float_pnl,
        total_pnl=total_pnl,
        position_ratio=total_position_value / initial_capital * 100 if initial_capital > 0 else 0,
        week_realized_pnl=week_realized_pnl,
        week_float_pnl=week_float_pnl,
        positions=positions,
        updated_at=datetime.now(),
    )

    sector_concentration = _compute_sector_concentration(positions, total_position_value)

    return PortfolioSummary(
        account=account_response,
        total_return=total_pnl,
        total_return_pct=(total_asset / initial_capital - 1) * 100 if initial_capital > 0 else 0,
        win_rate=win_rate,
        sector_concentration=sector_concentration,
    )


@router.get("/positions", response_model=list[PositionResponse])
async def get_positions(account: str = Query("stock", description="账户标识")):
    """Get current positions only."""
    position_list, _ = calculate_positions_from_db(account)[:2]
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
            entry_date=p.get('entry_date', ''),
            high_water_mark=hwm.get('high_price'),
            high_water_date=hwm.get('high_date'),
            days_since_high=hwm.get('days_since_high'),
        ))
    return positions


@router.post("/unfreeze")
async def unfreeze_funds(account: str = Query("stock", description="账户标识")):
    """Manually unfreeze all frozen funds.

    Used when trading exceptions cause funds to be incorrectly frozen.
    Moves all frozen_cash back to available_cash and cancels any stuck orders.
    """
    db = SessionLocal()
    try:
        acct = db.query(PaperAccountInfo).filter(PaperAccountInfo.account_id == account).first()
        if not acct:
            raise HTTPException(status_code=404, detail="账户信息不存在")

        frozen = float(acct.frozen_cash or 0)
        available = float(acct.available_cash or 0)

        if frozen <= 0:
            return {
                "success": True,
                "message": "没有冻结资金需要解冻",
                "unfrozen_amount": 0,
                "available_cash": available,
                "frozen_cash": 0,
                "orders_cancelled": 0,
            }

        stuck_count = db.query(PaperOrder).filter(
            PaperOrder.account_id == account,
            PaperOrder.status.in_(['提交中', '未成交'])
        ).count()

        if stuck_count > 0:
            db.query(PaperOrder).filter(
                PaperOrder.account_id == account,
                PaperOrder.status.in_(['提交中', '未成交'])
            ).update(
                {PaperOrder.status: '已撤销', PaperOrder.updated_at: datetime.now().isoformat()},
                synchronize_session=False
            )

        new_available = available + frozen
        acct.available_cash = new_available
        acct.frozen_cash = 0
        acct.updated_at = datetime.now().isoformat()
        db.commit()

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
    finally:
        db.close()



@router.post("/adjust-capital")
async def adjust_capital(req: CapitalAdjustRequest, account: str = Query("stock", description="账户标识")):
    """手动调整可用资金（入金为正，出金为负），用于修正总资产。

    调整会记录到 paper_capital_adjustments，并在每日快照与权益曲线回放中生效。
    """
    if not math.isfinite(req.amount) or abs(req.amount) < 0.005:
        raise HTTPException(status_code=400, detail="调整金额必须是非零数字")

    db = SessionLocal()
    try:
        acct = db.query(PaperAccountInfo).filter(PaperAccountInfo.account_id == account).first()
        if not acct:
            raise HTTPException(status_code=404, detail="账户信息不存在")

        current_cash = float(acct.available_cash or 0)
        new_cash = current_cash + req.amount
        if new_cash < 0:
            raise HTTPException(status_code=400, detail=f"可用资金不足，当前可用 ¥{current_cash:,.2f}，无法出金 ¥{abs(req.amount):,.2f}")

        acct.available_cash = new_cash
        acct.updated_at = datetime.now().isoformat()
        db.add(PaperCapitalAdjustment(
            account_id=account,
            amount=round(req.amount, 2),
            balance_after=round(new_cash, 2),
            note=(req.note or "")[:200],
            created_at=datetime.now().isoformat(),
        ))
        db.commit()
        return {
            "success": True,
            "amount": round(req.amount, 2),
            "available_cash": round(new_cash, 2),
            "message": f"资金调整成功，可用资金 ¥{new_cash:,.2f}",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"资金调整失败: {str(e)}")
    finally:
        db.close()


@router.post("/daily-snapshot")
async def trigger_daily_snapshot(date: str = Query(None, description="Target date YYYY-MM-DD, defaults to today"),
                                 account: str = Query("stock", description="账户标识")):
    """Manually trigger a daily portfolio snapshot.

    Computes current positions and total_asset (valued at market prices for today,
    at cost for historical dates) and persists to PostgreSQL paper_daily_snapshot.
    """
    result = save_daily_snapshot(target_date=date, account=account)
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', 'Snapshot failed'))
    return result


@router.get("/equity-history", response_model=list[EquityPoint])
async def get_equity_history(days: int = Query(60, ge=1, le=365),
                             account: str = Query("stock", description="账户标识")):
    """
    Get daily equity curve = available_cash + position_value on each day.

    历史日使用持仓成本价估值，当日使用实时市价估值，
    确保权益曲线与 total_asset 一致。
    """
    from datetime import datetime as dt, timedelta

    db = SessionLocal()
    try:
        acct = db.query(PaperAccountInfo).filter(PaperAccountInfo.account_id == account).first()
        initial_capital = float(acct.initial_capital) if acct else 1000000.0

        all_trades = db.query(PaperTrade).filter(
            PaperTrade.account_id == account,
            (PaperTrade.voided == 0) | (PaperTrade.voided == None)
        ).order_by(PaperTrade.trade_date, PaperTrade.id).all()

        snapshots = {}
        for snap in db.query(PaperDailySnapshot).filter(
            PaperDailySnapshot.account_id == account
        ).order_by(PaperDailySnapshot.trade_date).all():
            snapshots[snap.trade_date] = snap.total_asset

        adjustments_by_date: dict[str, float] = {}
        for adj in db.query(PaperCapitalAdjustment).filter(
            PaperCapitalAdjustment.account_id == account
        ).order_by(PaperCapitalAdjustment.created_at).all():
            d = (adj.created_at or '')[:10]
            if d:
                adjustments_by_date[d] = adjustments_by_date.get(d, 0) + float(adj.amount or 0)
    finally:
        db.close()

    # 按日期分组 trade
    trades_by_date = {}
    for t in all_trades:
        td = t.trade_date or (t.created_at[:10] if t.created_at else None)
        if td:
            trades_by_date.setdefault(td, []).append(t)

    if not trades_by_date:
        return []

    sorted_dates = sorted(trades_by_date.keys())
    min_trade_date = dt.strptime(sorted_dates[0], "%Y-%m-%d")
    today = dt.now()
    start_date = today - timedelta(days=days + 5)
    if start_date < min_trade_date:
        start_date = min_trade_date

    today_str = today.strftime("%Y-%m-%d")
    current_positions, _account, _realized, _winrate = calculate_positions_from_db(account)
    symbols = [p['symbol'] for p in current_positions]
    realtime_prices = get_realtime_prices(symbols) if symbols else {}

    # ── 逐日重放交易，计算每日权益 ──
    available_cash = initial_capital
    positions = {}
    adj_accum = 0.0

    for d in sorted_dates:
        if d >= start_date.strftime("%Y-%m-%d"):
            break
        for t in trades_by_date.get(d, []):
            available_cash, positions = _apply_trade(t, available_cash, positions)
        adj_accum += adjustments_by_date.get(d, 0)

    yesterday = today - timedelta(days=1)
    result = []
    current = start_date
    prev_equity = None
    while current <= yesterday and len(result) < days:
        date_str = current.strftime("%Y-%m-%d")

        for t in trades_by_date.get(date_str, []):
            available_cash, positions = _apply_trade(t, available_cash, positions)
        adj_accum += adjustments_by_date.get(date_str, 0)

        if date_str in snapshots:
            equity = snapshots[date_str]
        elif date_str == today_str:
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
                        price = sum(l['price'] * l['volume'] for l in lots) / total_vol
                    position_value += price * total_vol
            equity = available_cash + adj_accum + position_value
        elif date_str not in trades_by_date and prev_equity is not None:
            # 非交易日且无快照：权益不变（避免成本估值与市价估值跳变）
            equity = prev_equity
        else:
            position_value = sum(
                l['price'] * l['volume']
                for lots in positions.values()
                for l in lots
            )
            equity = available_cash + adj_accum + position_value

        daily_pnl = round(equity - prev_equity, 2) if prev_equity is not None else 0.0
        prev_equity = equity
        result.append(EquityPoint(date=date_str, equity=round(equity, 2), daily_pnl=daily_pnl))

        current += timedelta(days=1)

    if len(result) > days:
        result = result[-days:]

    return result


@router.get("/daily-pnl-breakdown", response_model=list[DailyPnlBreakdown])
async def get_daily_pnl_breakdown(
    days: int = Query(30, ge=1, le=60),
    sort_dir: str = Query("desc", regex="^(asc|desc)$"),
    account: str = Query("stock", description="账户标识"),
):
    """
    每日盈亏明细 — 含个股贡献分解 (Tushare 历史收盘价)。
    sort_dir: asc=日期升序, desc=日期降序
    """
    from datetime import datetime as dt, timedelta

    db = SessionLocal()
    try:
        acct = db.query(PaperAccountInfo).filter(PaperAccountInfo.account_id == account).first()
        initial_capital = float(acct.initial_capital) if acct else 100000.0

        all_trades = db.query(PaperTrade).filter(
            PaperTrade.account_id == account,
            (PaperTrade.voided == 0) | (PaperTrade.voided == None)
        ).order_by(
            func.coalesce(PaperTrade.trade_date, func.substr(PaperTrade.created_at, 1, 10)),
            PaperTrade.id,
        ).all()
    finally:
        db.close()

    if not all_trades:
        return []

    today = dt.now()
    trades_by_date: dict[str, list] = {}
    all_stocks: set[str] = set()
    min_date_str = None
    for t in all_trades:
        td = t.trade_date or (t.created_at[:10] if t.created_at else None)
        if td:
            trades_by_date.setdefault(td, []).append(t)
            all_stocks.add(t.symbol)
            if min_date_str is None or td < min_date_str:
                min_date_str = td

    if not min_date_str:
        return []

    sorted_dates = sorted(trades_by_date.keys())
    start_date = dt.strptime(min_date_str, "%Y-%m-%d")
    cutoff_date = today - timedelta(days=days)

    # ── 批量拉取 Tushare 历史收盘价 ──
    close_cache: dict[tuple[str, str], float] = {}
    try:
        from app.core.trading._api_config import get_tushare_pro
        pro = get_tushare_pro()
        ts_start = start_date.strftime("%Y%m%d")
        ts_end = today.strftime("%Y%m%d")
        for sym in all_stocks:
            ts_code = _normalize_to_ts_code(sym)
            try:
                df = pro.daily(
                    ts_code=ts_code, start_date=ts_start, end_date=ts_end,
                    fields="ts_code,trade_date,close",
                )
                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        close_cache[(sym, str(row["trade_date"]))] = float(row["close"])
            except Exception:
                pass
    except Exception as e:
        print(f"[daily-pnl-breakdown] Tushare fetch failed: {e}", flush=True)

    def _get_close(sym: str, date_str: str) -> float | None:
        return close_cache.get((sym, date_str.replace("-", "")))

    def _get_prev_trade_date(date_str: str) -> str | None:
        d = dt.strptime(date_str, "%Y-%m-%d")
        for _ in range(10):
            d -= timedelta(days=1)
            prev = d.strftime("%Y-%m-%d")
            for sym in all_stocks:
                if _get_close(sym, prev):
                    return prev
        return None

    # ── 逐日 FIFO 回放 ──
    cash = initial_capital
    positions: dict[str, list[dict]] = {}
    prev_close_cache: dict[str, float] = {}

    # 快进到 cutoff_date 之前
    for d in sorted_dates:
        if d >= cutoff_date.strftime("%Y-%m-%d"):
            break
        for t in trades_by_date.get(d, []):
            cash, positions = _apply_trade(t, cash, positions)

    result: list[DailyPnlBreakdown] = []
    for d in sorted_dates:
        if d < cutoff_date.strftime("%Y-%m-%d"):
            continue

        prev_date = _get_prev_trade_date(d)
        for sym in all_stocks:
            if prev_date:
                pc = _get_close(sym, prev_date)
                if pc:
                    prev_close_cache[sym] = pc

        day_realized = 0.0
        day_realized_by_stock: dict[str, float] = {}
        for t in trades_by_date.get(d, []):
            if hasattr(t, "direction") and t.direction == "卖出":
                sell_price = t.price
                prev_close = prev_close_cache.get(t.symbol, sell_price)
                daily_incr = t.volume * (sell_price - prev_close)
                day_realized += daily_incr
                sym = t.symbol
                day_realized_by_stock[sym] = day_realized_by_stock.get(sym, 0) + daily_incr
            cash, positions = _apply_trade(t, cash, positions)

        stocks_pnl: list[DailyStockPnl] = []
        day_float = 0.0
        touched: set[str] = set()

        for sym_raw, lots in positions.items():
            sym = str(sym_raw) if not isinstance(sym_raw, str) else sym_raw
            total_vol = sum(l["volume"] for l in lots)
            if total_vol <= 0:
                continue
            touched.add(sym)
            close_today = _get_close(sym, d) or 0
            prev_close = prev_close_cache.get(sym, close_today)
            stock_float = total_vol * (close_today - prev_close) if close_today and prev_close else 0.0
            stock_realized = day_realized_by_stock.get(sym, 0.0)
            day_float += stock_float
            stocks_pnl.append(DailyStockPnl(
                symbol=sym,
                name=get_stock_name(sym),
                volume=total_vol,
                close_price=close_today,
                prev_close=prev_close,
                float_pnl=round(stock_float, 2),
                realized_pnl=round(stock_realized, 2),
            ))

        for sym, realized in day_realized_by_stock.items():
            if sym not in touched:
                stocks_pnl.append(DailyStockPnl(
                    symbol=sym, name=get_stock_name(sym), volume=0,
                    float_pnl=0, realized_pnl=round(realized, 2),
                ))

        result.append(DailyPnlBreakdown(
            date=d,
            daily_pnl=round(day_realized + day_float, 2),
            realized_total=round(day_realized, 2),
            float_total=round(day_float, 2),
            stocks=sorted(stocks_pnl, key=lambda s: abs(s.float_pnl + s.realized_pnl), reverse=True),
        ))

        if len(result) >= days:
            break

    if sort_dir == "desc":
        result.reverse()

    return result




@router.get("/daily-pnl-breakdown/date", response_model=DailyPnlBreakdown)
async def get_daily_pnl_breakdown_by_date(date: str = Query(..., description="Target date YYYY-MM-DD"),
                                          account: str = Query("stock", description="账户标识")):
    """获取指定日期的个股盈亏明细（懒加载用）"""
    from datetime import datetime as dt, timedelta

    db = SessionLocal()
    try:
        acct = db.query(PaperAccountInfo).filter(PaperAccountInfo.account_id == account).first()
        initial_capital = float(acct.initial_capital) if acct else 100000.0

        all_trades = db.query(PaperTrade).filter(
            PaperTrade.account_id == account,
            (PaperTrade.voided == 0) | (PaperTrade.voided == None)
        ).order_by(
            func.coalesce(PaperTrade.trade_date, func.substr(PaperTrade.created_at, 1, 10)),
            PaperTrade.id,
        ).all()
    finally:
        db.close()

    if not all_trades:
        raise HTTPException(status_code=404, detail="No trades found")

    trades_by_date: dict[str, list] = {}
    all_stocks: set[str] = set()
    for t in all_trades:
        td = t.trade_date or (t.created_at[:10] if t.created_at else None)
        if td:
            trades_by_date.setdefault(td, []).append(t)
            all_stocks.add(t.symbol)

    sorted_dates = sorted(trades_by_date.keys())
    has_trades_up_to_date = any(d <= date for d in sorted_dates)
    if not has_trades_up_to_date:
        return DailyPnlBreakdown(date=date, daily_pnl=0, realized_total=0, float_total=0, stocks=[])

    # ── FIFO replay up to target date ──
    cash = initial_capital
    positions: dict[str, list[dict]] = {}

    for d in sorted_dates:
        if d > date:
            break
        for t in trades_by_date.get(d, []):
            cash, positions = _apply_trade(t, cash, positions)

    # ── Fetch close prices for target date and previous trading day ──
    # 当天不是交易日时，回退到最近交易日
    close_prices: dict[str, float] = {}
    prev_close_prices: dict[str, float] = {}
    try:
        from app.core.trading._api_config import get_tushare_pro
        pro = get_tushare_pro()
        held = [s for s, lots in positions.items() if sum(l["volume"] for l in lots) > 0]

        def _fetch_close_for_date(symbols: list, date_str: str) -> dict[str, float]:
            """获取指定日期的收盘价，无数据则返回空"""
            result: dict[str, float] = {}
            if not symbols:
                return result
            try:
                df = pro.daily(
                    ts_code=",".join([_normalize_to_ts_code(s) for s in symbols]),
                    trade_date=date_str.replace("-", ""),
                    fields="ts_code,close",
                )
                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        for sym in symbols:
                            if _normalize_to_ts_code(sym) == row["ts_code"]:
                                result[sym] = float(row["close"])
                                break
            except Exception:
                pass
            return result

        # 1) 尝试获取当日收盘价
        close_prices = _fetch_close_for_date(held, date)
        actual_date = date

        # 2) 当日无数据（周末/节假日），向前查找最近交易日
        if not close_prices and held:
            ref_dt = dt.strptime(date, "%Y-%m-%d")
            for _ in range(10):
                ref_dt -= timedelta(days=1)
                ref_str = ref_dt.strftime("%Y-%m-%d")
                close_prices = _fetch_close_for_date(held, ref_str)
                if close_prices:
                    actual_date = ref_str
                    break

        # 3) 获取前一交易日收盘价（从 actual_date 往前找）
        if held:
            prev_dt = dt.strptime(actual_date, "%Y-%m-%d")
            for _ in range(15):
                prev_dt -= timedelta(days=1)
                prev_str = prev_dt.strftime("%Y-%m-%d")
                prev_close_prices = _fetch_close_for_date(list(all_stocks), prev_str)
                if prev_close_prices:
                    break
    except Exception as e:
        print(f"[daily-pnl-breakdown/date] Tushare fetch failed: {e}", flush=True)

    # ── Compute day realized P&L ──
    day_realized = 0.0
    day_realized_by_stock: dict[str, float] = {}
    for t in trades_by_date.get(date, []):
        if hasattr(t, "direction") and t.direction == "卖出":
            sell_price = t.price
            prev_close = prev_close_prices.get(t.symbol, sell_price)
            daily_incr = t.volume * (sell_price - prev_close)
            day_realized += daily_incr
            day_realized_by_stock[t.symbol] = day_realized_by_stock.get(t.symbol, 0) + daily_incr

    # ── Compute per-stock float P&L ──
    stocks_pnl: list[DailyStockPnl] = []
    day_float = 0.0
    touched: set[str] = set()

    for sym, lots in positions.items():
        total_vol = sum(l["volume"] for l in lots)
        if total_vol <= 0:
            continue
        touched.add(sym)
        close_today = close_prices.get(sym, 0)
        prev_close = prev_close_prices.get(sym, close_today)
        stock_float = total_vol * (close_today - prev_close) if close_today and prev_close else 0.0
        stock_realized = day_realized_by_stock.get(sym, 0.0)
        day_float += stock_float
        stocks_pnl.append(DailyStockPnl(
            symbol=sym,
            name=get_stock_name(sym),
            volume=total_vol,
            close_price=close_today,
            prev_close=prev_close,
            float_pnl=round(stock_float, 2),
            realized_pnl=round(stock_realized, 2),
        ))

    for sym, realized in day_realized_by_stock.items():
        if sym not in touched:
            stocks_pnl.append(DailyStockPnl(
                symbol=sym, name=get_stock_name(sym), volume=0,
                float_pnl=0, realized_pnl=round(realized, 2),
            ))

    return DailyPnlBreakdown(
        date=date,
        daily_pnl=round(day_realized + day_float, 2),
        realized_total=round(day_realized, 2),
        float_total=round(day_float, 2),
        stocks=sorted(stocks_pnl, key=lambda s: abs(s.float_pnl + s.realized_pnl), reverse=True),
    )


def _apply_trade(trade, cash: float, positions: dict) -> tuple:
    """将一笔成交应用到账户状态，返回 (new_cash, new_positions)

    支持 SQLAlchemy ORM 对象 (attr access) 和 dict (key access)。
    """
    # 兼容 ORM 对象和 dict
    if hasattr(trade, 'symbol'):
        symbol, direction, price, volume = trade.symbol, trade.direction, trade.price, trade.volume
    else:
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
