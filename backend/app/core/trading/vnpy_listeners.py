#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VN.PY → PostgreSQL 事件同步监听器。

监听 VN.PY 事件引擎的 OrderEvent / TradeEvent / AccountEvent / PositionEvent,
通过后台线程池异步写入 paper_orders / paper_trades / paper_account_info / paper_positions 表。

关键设计: 事件回调在主线程执行后立即返回, 实际的 PG 写入在后台线程中完成,
避免阻塞 VN.PY 事件引擎的消息循环。
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

import psycopg2

from vnpy.trader.event import (
    EVENT_ORDER,
    EVENT_TRADE,
    EVENT_ACCOUNT,
    EVENT_POSITION,
)
from vnpy.trader.object import (
    OrderData,
    TradeData,
    AccountData,
    PositionData,
)
from vnpy.trader.constant import Direction, Status
from vnpy.event import Event, EventEngine

logger = logging.getLogger(__name__)

# 共享后台写入线程池 (最多 2 个 worker, 避免过多的 PG 连接)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pg_sync")

# PG 连接超时 (秒), 确保网络异常时不会无限阻塞
_CONNECT_TIMEOUT = 3


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _now_iso() -> str:
    return datetime.now().isoformat()


def _marcus_symbol(symbol: str, exchange) -> str:
    """VN.PY symbol + exchange → Marcus 格式 (SH/SZ + code)"""
    exchange_str = str(exchange.value) if hasattr(exchange, 'value') else ""
    if exchange_str == "SSE":
        return f"SH{symbol}"
    elif exchange_str == "SZSE":
        return f"SZ{symbol}"
    elif exchange_str == "BSE":
        return f"BJ{symbol}"
    return symbol


def _get_conn(pg_params: dict):
    return psycopg2.connect(connect_timeout=_CONNECT_TIMEOUT, **pg_params)


class OrderEventListener:
    """订单事件 → paper_orders (异步写入)"""

    def __init__(self, pg_params: dict):
        self._pg_params = pg_params

    def register(self, event_engine: EventEngine) -> None:
        event_engine.register(EVENT_ORDER, self._safe_on_event)

    def _safe_on_event(self, event: Event) -> None:
        """事件回调 — 只提取数据, 立即返回"""
        try:
            order: OrderData = event.data
            status_map = {
                Status.SUBMITTING: "提交中",
                Status.NOTTRADED: "未成交",
                Status.PARTTRADED: "部分成交",
                Status.ALLTRADED: "全部成交",
                Status.CANCELLED: "已撤销",
                Status.REJECTED: "拒单",
            }
            params = (
                order.vt_orderid,
                _marcus_symbol(order.symbol, order.exchange),
                "买入" if order.direction == Direction.LONG else "卖出",
                order.price,
                order.volume,
                status_map.get(order.status, str(order.status)),
                order.traded,
                _now_iso(),
                getattr(order, 'reference', '') or '',
            )
            pg_params = self._pg_params
            _executor.submit(_sync_order, pg_params, params)
        except Exception:
            pass

    def shutdown(self) -> None:
        pass


def _sync_order(pg_params: dict, params: tuple) -> None:
    """后台线程: 写入 paper_orders"""
    orderid, symbol, direction, price, volume, status, traded, now, reason = params
    try:
        conn = _get_conn(pg_params)
        cur = conn.cursor()
        cur.execute(
            "SELECT orderid FROM paper_orders WHERE orderid = %s AND account_id = 'stock'",
            (orderid,),
        )
        if cur.fetchone():
            cur.execute(
                "UPDATE paper_orders SET status = %s, traded = %s, "
                "updated_at = %s WHERE orderid = %s AND account_id = 'stock'",
                (status, traded, now, orderid),
            )
        else:
            cur.execute(
                "INSERT INTO paper_orders "
                "(orderid, account_id, symbol, direction, price, volume, status, "
                "traded, created_at, updated_at, reason) "
                "VALUES (%s, 'stock', %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (orderid, symbol, direction, price, volume, status,
                 traded, now, now, reason),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("[OrderListener] PG write failed: %s", e)


class TradeEventListener:
    """成交事件 → paper_trades (异步写入)"""

    def __init__(self, pg_params: dict):
        self._pg_params = pg_params

    def register(self, event_engine: EventEngine) -> None:
        event_engine.register(EVENT_TRADE, self._safe_on_event)

    def _safe_on_event(self, event: Event) -> None:
        """事件回调 — 只提取数据, 立即返回"""
        try:
            trade: TradeData = event.data
            params = (
                trade.orderid,
                _marcus_symbol(trade.symbol, trade.exchange),
                "买入" if trade.direction == Direction.LONG else "卖出",
                trade.price,
                trade.volume,
                trade.price * trade.volume,
                datetime.now().strftime("%Y-%m-%d"),
                _now_iso(),
            )
            pg_params = self._pg_params
            _executor.submit(_sync_trade, pg_params, params)
        except Exception:
            pass

    def shutdown(self) -> None:
        pass


def _sync_trade(pg_params: dict, params: tuple) -> None:
    """后台线程: 写入 paper_trades"""
    orderid, symbol, direction, price, volume, amount, trade_date, now = params
    try:
        conn = _get_conn(pg_params)
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM paper_trades WHERE orderid = %s AND symbol = %s "
            "AND direction = %s AND price = %s AND volume = %s AND account_id = 'stock'",
            (orderid, symbol, direction, price, volume),
        )
        if cur.fetchone():
            conn.close()
            return
        cur.execute(
            "INSERT INTO paper_trades "
            "(orderid, account_id, symbol, direction, price, volume, amount, profit, "
            "created_at, trade_date, reason) "
            "VALUES (%s, 'stock', %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (orderid, symbol, direction, price, volume, amount,
             0, now, trade_date, ''),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("[TradeListener] PG write failed: %s", e)


class AccountEventListener:
    """账户事件 → paper_account_info (异步写入)"""

    def __init__(self, pg_params: dict):
        self._pg_params = pg_params

    def register(self, event_engine: EventEngine) -> None:
        event_engine.register(EVENT_ACCOUNT, self._safe_on_event)

    def _safe_on_event(self, event: Event) -> None:
        """事件回调 — 只提取数据, 立即返回"""
        try:
            account: AccountData = event.data
            params = (account.balance, account.frozen, _now())
            pg_params = self._pg_params
            _executor.submit(_sync_account, pg_params, params)
        except Exception:
            pass

    def shutdown(self) -> None:
        pass


def _sync_account(pg_params: dict, params: tuple) -> None:
    """后台线程: 写入 paper_account_info"""
    balance, frozen, now = params
    try:
        conn = _get_conn(pg_params)
        cur = conn.cursor()
        cur.execute(
            "UPDATE paper_account_info SET available_cash = %s, "
            "frozen_cash = %s, updated_at = %s WHERE account_id = 'stock'",
            (balance, frozen, now),
        )
        if cur.rowcount == 0:
            cur.execute(
                "INSERT INTO paper_account_info "
                "(account_id, initial_capital, available_cash, frozen_cash, updated_at) "
                "VALUES ('stock', %s, %s, %s, %s)",
                (balance, balance, frozen, now),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("[AccountListener] PG write failed: %s", e)


class PositionEventListener:
    """持仓事件 → paper_positions (异步写入)"""

    def __init__(self, pg_params: dict):
        self._pg_params = pg_params

    def register(self, event_engine: EventEngine) -> None:
        event_engine.register(EVENT_POSITION, self._safe_on_event)

    def _safe_on_event(self, event: Event) -> None:
        """事件回调 — 只提取数据, 立即返回"""
        try:
            position: PositionData = event.data
            if position.direction != Direction.LONG:
                return
            params = (
                _marcus_symbol(position.symbol, position.exchange),
                position.volume,
                position.frozen,
                position.price,
                _now(),
                datetime.now().strftime("%Y-%m-%d"),
            )
            pg_params = self._pg_params
            _executor.submit(_sync_position, pg_params, params)
        except Exception:
            pass

    def shutdown(self) -> None:
        pass


def _sync_position(pg_params: dict, params: tuple) -> None:
    """后台线程: 写入 paper_positions"""
    symbol, volume, frozen, price, now, today = params
    try:
        conn = _get_conn(pg_params)
        cur = conn.cursor()
        if volume <= 0:
            cur.execute(
                "DELETE FROM paper_positions WHERE account_id = 'stock' AND symbol = %s",
                (symbol,),
            )
            conn.commit()
            conn.close()
            return
        cur.execute(
            "SELECT symbol FROM paper_positions WHERE account_id = 'stock' AND symbol = %s",
            (symbol,),
        )
        if cur.fetchone():
            cur.execute(
                "UPDATE paper_positions SET volume = %s, frozen = %s, "
                "avg_price = %s, "
                "highest_price = GREATEST(COALESCE(highest_price, 0), %s), "
                "updated_at = %s WHERE account_id = 'stock' AND symbol = %s",
                (volume, frozen, price, price, now, symbol),
            )
        else:
            cur.execute(
                "INSERT INTO paper_positions "
                "(account_id, symbol, entry_date, highest_price, updated_at, "
                "volume, frozen, avg_price) "
                "VALUES ('stock', %s, %s, %s, %s, %s, %s, %s)",
                (symbol, today, price, now, volume, frozen, price),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("[PositionListener] PG write failed: %s", e)
