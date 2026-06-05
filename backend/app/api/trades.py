# -*- coding: utf-8 -*-
"""
Trades API endpoints.
"""
import sys
import time
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.models.trade import TradeRequest, TradeResponse, OrderResponse, TradeHistoryResponse

settings = get_settings()

router = APIRouter(prefix="/trades", tags=["Trades"])

# Stock name cache
_stock_name_cache = {}


def _get_stock_name(symbol: str) -> str:
    """Lookup stock name from stock_pool.db."""
    if symbol in _stock_name_cache:
        return _stock_name_cache[symbol]

    pool_db = settings.data_dir / "stock_pool.db"
    if pool_db.exists():
        try:
            conn = sqlite3.connect(str(pool_db))
            conn.row_factory = sqlite3.Row
            curs = conn.cursor()
            code = symbol[2:] if len(symbol) > 4 and symbol[:2] in ('SH', 'SZ', 'BJ') else symbol
            curs.execute("SELECT name FROM stock_pool WHERE symbol = ? OR ts_code = ?", (code, symbol))
            row = curs.fetchone()
            conn.close()
            if row and row['name']:
                name = row['name'].strip()
                _stock_name_cache[symbol] = name
                return name
        except Exception:
            pass

    _stock_name_cache[symbol] = symbol
    return symbol


def _get_db_conn(db_file, timeout=30):
    """获取数据库连接（统一配置）"""
    conn = sqlite3.connect(str(db_file), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _execute_with_retry(func, max_retries=3, delay=0.5):
    """数据库操作重试装饰器"""
    last_error = None
    for attempt in range(max_retries):
        try:
            return func()
        except sqlite3.OperationalError as e:
            last_error = e
            if "locked" in str(e).lower() and attempt < max_retries - 1:
                time.sleep(delay * (attempt + 1))  # 递增等待
                continue
            raise
    raise last_error


@router.post("", response_model=TradeResponse)
async def execute_trade(trade: TradeRequest):
    """
    Execute a trade (buy or sell).
    Note: This is paper trading - no real money involved.
    """
    try:
        from app.core.trading.marcus_trade import MarcusVNPyExecutor

        executor = MarcusVNPyExecutor()

        if trade.side.lower() == "buy":
            result = executor.buy(
                symbol=trade.symbol,
                price=trade.price,
                volume=trade.volume,
                reason=trade.reason or "",
            )
        else:
            result = executor.sell(
                symbol=trade.symbol,
                price=trade.price,
                volume=trade.volume,
                reason=trade.reason or "",
            )

        return TradeResponse(
            order_id=result.get("order_id", ""),
            status=result.get("status", "executed"),
            symbol=trade.symbol,
            direction="买入" if trade.side.lower() == "buy" else "卖出",
            price=trade.price,
            volume=trade.volume,
            amount=trade.price * trade.volume,
            timestamp=datetime.now(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=TradeHistoryResponse)
async def get_trade_history(
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    limit: int = Query(20, ge=1, le=100, description="Number of records"),
    page: int = Query(1, ge=1, description="Page number"),
):
    """Get trade history."""
    db_file = settings.data_dir / "trades.db"
    if not db_file.exists():
        return TradeHistoryResponse(trades=[], total=0, page=page, page_size=limit)

    def _query():
        conn = _get_db_conn(db_file)
        curs = conn.cursor()

        # Build query
        where_clause = ""
        params = []
        if symbol:
            where_clause = "WHERE symbol = ?"
            params.append(symbol)

        # Get total count
        count_sql = f"SELECT COUNT(*) as cnt FROM trades {where_clause}"
        curs.execute(count_sql, params)
        total = curs.fetchone()["cnt"]

        # Get paginated trades
        offset = (page - 1) * limit
        sql = f"""
            SELECT orderid, symbol, direction, price, volume, created_at
            FROM trades
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """
        curs.execute(sql, params + [limit, offset])
        rows = curs.fetchall()

        trades = []
        for row in rows:
            sym = row["symbol"]
            trades.append(OrderResponse(
                order_id=row["orderid"],
                symbol=sym,
                name=_get_stock_name(sym),
                direction=row["direction"],
                price=row["price"],
                volume=row["volume"],
                status="completed",
                traded=row["volume"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["created_at"]),
            ))

        conn.close()
        return trades, total

    try:
        trades, total = _execute_with_retry(_query)
    except sqlite3.OperationalError as e:
        raise HTTPException(status_code=503, detail=f"Database busy, please retry: {str(e)}")

    return TradeHistoryResponse(
        trades=trades,
        total=total,
        page=page,
        page_size=limit,
    )


@router.get("/orders")
async def get_pending_orders(
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    status: Optional[str] = Query(None, description="Filter by status: 提交中/未成交/部分成交/已撤销"),
    limit: int = Query(50, ge=1, le=200),
):
    """
    Get pending/active orders from the paper trading engine.
    Used by Pi agent to check order status before making new trades.
    """
    try:
        from app.core.trading.marcus_trade import MarcusVNPyExecutor
        executor = MarcusVNPyExecutor()
        orders = executor.engine.get_orders(symbol=symbol, status=status, limit=limit)
        return {
            "orders": orders,
            "count": len(orders),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{order_id}", response_model=OrderResponse)
async def get_trade(order_id: str):
    """Get specific trade by order ID."""
    db_file = settings.data_dir / "trades.db"
    if not db_file.exists():
        raise HTTPException(status_code=404, detail="Trade not found")

    def _query():
        conn = _get_db_conn(db_file)
        curs = conn.cursor()

        curs.execute("""
            SELECT orderid, symbol, direction, price, volume, created_at
            FROM trades WHERE orderid = ?
        """, (order_id,))
        row = curs.fetchone()

        if not row:
            conn.close()
            return None

        trade = OrderResponse(
            order_id=row["orderid"],
            symbol=row["symbol"],
            direction=row["direction"],
            price=row["price"],
            volume=row["volume"],
            status="completed",
            traded=row["volume"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["created_at"]),
        )

        conn.close()
        return trade

    try:
        trade = _execute_with_retry(_query)
    except sqlite3.OperationalError as e:
        raise HTTPException(status_code=503, detail=f"Database busy, please retry: {str(e)}")

    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade


@router.delete("/{order_id}/cancel")
async def cancel_order(order_id: str):
    """
    Cancel a pending order by order ID.
    Only orders with status '提交中' or '未成交' can be cancelled.
    """
    try:
        from app.core.trading.marcus_trade import MarcusVNPyExecutor
        executor = MarcusVNPyExecutor()
        success = executor.engine.cancel_order(order_id)
        if success:
            return {
                "status": "cancelled",
                "order_id": order_id,
                "timestamp": datetime.now().isoformat(),
            }
        else:
            raise HTTPException(status_code=400, detail=f"无法撤销订单 {order_id}，可能已成交或不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
