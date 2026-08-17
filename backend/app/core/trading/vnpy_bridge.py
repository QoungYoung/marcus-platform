#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VN.PY 桥接层 — 将 VN.PY 原版 PaperAccount 接入 Marcus 交易系统。

用法:
    bridge = VNPyBridge()
    bridge.start()
    bridge.send_order("SH600519", "买入", 1700.0, 100, "测试")
    account = bridge.get_account()
    positions = bridge.get_positions()
    bridge.stop()
"""

import os
import sys
import json
import time
import threading
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List
from dataclasses import dataclass

import psycopg2

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication  # noqa: E402

from vnpy.trader.engine import MainEngine  # noqa: E402
from vnpy.trader.constant import Direction, Offset, OrderType, Exchange, Status  # noqa: E402
from vnpy.trader.object import (  # noqa: E402
    OrderRequest, ContractData, TickData, SubscribeRequest,
    OrderData, TradeData, AccountData, PositionData,
)
from vnpy.trader.event import (  # noqa: E402
    EVENT_CONTRACT, EVENT_TICK, EVENT_ORDER, EVENT_TRADE,
    EVENT_ACCOUNT, EVENT_POSITION, EVENT_TIMER,
)
from vnpy.event import Event  # noqa: E402
from vnpy_paperaccount import PaperAccountApp  # noqa: E402

from .vnpy_listeners import (
    OrderEventListener,
    TradeEventListener,
    AccountEventListener,
    PositionEventListener,
)

logger = logging.getLogger(__name__)

GATEWAY_NAME = "PAPER"

# Marcus 代码 → VN.PY (symbol, exchange) 映射
EXCHANGE_MAP = {
    "SH": Exchange.SSE,
    "SZ": Exchange.SZSE,
    "BJ": Exchange.BSE,
}


@dataclass
class PendingOrder:
    """等待成交的订单追踪"""
    orderid: str
    event: threading.Event
    result: Optional[dict] = None


class VNPyBridge:
    """VN.PY × Marcus 交易桥接器 (单例)"""

    def __init__(
        self,
        db_url: str = None,
        initial_capital: float = 100000.0,
    ):
        self._db_url = db_url or os.getenv(
            "DATABASE_URL",
            "postgresql://marcus:marcus123@localhost:5432/marcus_trading",
        )
        self._initial_capital = initial_capital
        self._available_cash = initial_capital
        self._frozen_cash = 0.0

        self._main_engine: Optional[MainEngine] = None
        self._paper_engine = None
        self._qapp: Optional[QApplication] = None
        self._ready = threading.Event()
        self._stop_event = threading.Event()

        # 订单等待机制 — 同步化异步成交
        self._pending_orders: Dict[str, PendingOrder] = {}
        self._order_lock = threading.Lock()

        # 合约缓存
        self._registered_contracts: set = set()

        # PostgreSQL 事件监听器 (延迟初始化)
        self._order_listener: Optional[OrderEventListener] = None
        self._trade_listener: Optional[TradeEventListener] = None
        self._account_listener: Optional[AccountEventListener] = None
        self._position_listener: Optional[PositionEventListener] = None

    # ── 生命周期 ──────────────────────────────────────────────

    def start(self) -> None:
        """启动 VN.PY 事件引擎和模拟账户"""
        if self._main_engine is not None:
            return

        # 初始化 Qt (offscreen, headless)
        self._qapp = QApplication.instance() or QApplication(sys.argv)

        # 加载已有账户状态
        self._load_account_from_pg()

        # 清理 VN.PY 持久化文件 (避免历史仓位累积)
        self._clear_paper_data_files()

        # 创建主引擎
        self._main_engine = MainEngine()
        self._paper_engine = self._main_engine.add_app(PaperAccountApp)
        self._paper_engine.instant_trade = True

        # 注册内部事件监听 (成交回调)
        self._main_engine.event_engine.register(
            EVENT_TRADE, self._on_trade_event
        )
        self._main_engine.event_engine.register(
            EVENT_ORDER, self._on_order_event
        )

        # 初始化 PostgreSQL 同步监听器
        pg_params = self._parse_db_url(self._db_url)
        self._order_listener = OrderEventListener(pg_params)
        self._trade_listener = TradeEventListener(pg_params)
        self._account_listener = AccountEventListener(pg_params)
        self._position_listener = PositionEventListener(pg_params)

        for listener in [
            self._order_listener,
            self._trade_listener,
            self._account_listener,
            self._position_listener,
        ]:
            listener.register(self._main_engine.event_engine)

        # 注入正确的账户余额 (PaperAccount 默认 1000000, 需要覆盖)
        time.sleep(0.1)  # 等待 PaperAccount 初始化完成
        acct_data = AccountData(
            gateway_name=GATEWAY_NAME,
            accountid=GATEWAY_NAME,
            balance=self._available_cash,
            frozen=self._frozen_cash,
        )
        self._main_engine.event_engine.put(Event(EVENT_ACCOUNT, acct_data))

        self._ready.set()
        logger.info(
            "[VNPyBridge] 已启动, 可用资金=%.2f",
            self._available_cash,
        )

    def stop(self) -> None:
        """停止 VN.PY 事件引擎"""
        self._stop_event.set()
        if self._main_engine is not None:
            try:
                self._main_engine.event_engine.stop()
            except Exception:
                pass
            self._main_engine = None
            self._paper_engine = None
        logger.info("[VNPyBridge] 已停止")

    # ── 符号规范化 ────────────────────────────────────────────

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """Marcus 代码 → 纯数字代码 (如 SH600519 → 600519)"""
        symbol = symbol.strip().upper()
        if symbol.startswith(("SH", "SZ", "BJ")) and len(symbol) == 8:
            return symbol[2:]
        return symbol

    @staticmethod
    def _get_exchange(symbol: str) -> Exchange:
        """从 Marcus 代码提取交易所"""
        symbol = symbol.strip().upper()
        if symbol.startswith("SH") or (
            symbol.isdigit() and symbol[:3] in ("600", "601", "603", "605", "688")
        ):
            return Exchange.SSE
        if symbol.startswith("SZ") or (
            symbol.isdigit()
            and (
                symbol[:3] in ("000", "001", "002", "003", "004")
                or symbol[:2] == "30"
            )
        ):
            return Exchange.SZSE
        if symbol.startswith("BJ"):
            return Exchange.BSE
        return Exchange.SSE

    @staticmethod
    def _make_vt_symbol(symbol: str) -> str:
        """Marcus 代码 → VN.PY vt_symbol (如 SH600519 → 600519.SSE)"""
        code = VNPyBridge._normalize_symbol(symbol)
        exchange = VNPyBridge._get_exchange(symbol)
        # Exchange.value returns e.g. "SSE", "SZSE"
        ex_str = str(exchange.value) if hasattr(exchange, 'value') else "SSE"
        return f"{code}.{ex_str}"

    # ── 合约管理 ──────────────────────────────────────────────

    def _ensure_contract(self, symbol: str, name: str = "") -> None:
        """确保合约已在模拟引擎中注册"""
        code = self._normalize_symbol(symbol)
        if code in self._registered_contracts:
            return

        exchange = self._get_exchange(symbol)
        contract = ContractData(
            symbol=code,
            exchange=exchange,
            name=name or code,
            product=1,  # EQUITY
            size=1,
            pricetick=0.01,
            min_volume=1,
            gateway_name=GATEWAY_NAME,
            net_position=True,  # 股票用净持仓模式
        )
        self._main_engine.event_engine.put(
            Event(EVENT_CONTRACT, contract)
        )
        self._registered_contracts.add(code)

    def _send_tick(self, symbol: str, price: float) -> None:
        """向模拟引擎推送当前行情 (触发撮合)

        ask/bid 都设为委托价: paper engine 的 cross_order 要求
        - 买入: order.price >= ask_price_1 → 成交
        - 卖出: order.price <= bid_price_1 → 成交
        """
        code = self._normalize_symbol(symbol)
        exchange = self._get_exchange(symbol)
        tick = TickData(
            symbol=code,
            exchange=exchange,
            last_price=price,
            ask_price_1=price,
            bid_price_1=price,
            gateway_name=GATEWAY_NAME,
            datetime=datetime.now(),
        )
        self._main_engine.event_engine.put(Event(EVENT_TICK, tick))
        time.sleep(0.05)  # 让事件引擎处理

    # ── 交易接口 ──────────────────────────────────────────────

    def send_order(
        self,
        symbol: str,
        direction: str,
        price: float,
        volume: int,
        reason: str = "",
    ) -> Optional[str]:
        """
        发送订单并同步等待成交。

        Args:
            symbol: Marcus 股票代码 (如 SH600519)
            direction: "买入" 或 "卖出"
            price: 委托价格
            volume: 委托数量
            reason: 交易原因

        Returns:
            成交后返回 order_id, 失败返回 None
        """
        if not self._ready.is_set():
            logger.error("[VNPyBridge] 引擎未就绪")
            return None

        self._ensure_contract(symbol)
        self._send_tick(symbol, price)

        code = self._normalize_symbol(symbol)
        exchange = self._get_exchange(symbol)

        is_buy = direction in ("买入", "buy", "BUY", "LONG")
        vn_direction = Direction.LONG if is_buy else Direction.SHORT
        vn_offset = Offset.OPEN if is_buy else Offset.CLOSE

        req = OrderRequest(
            symbol=code,
            exchange=exchange,
            direction=vn_direction,
            type=OrderType.LIMIT,
            volume=volume,
            price=price,
            offset=vn_offset,
            reference=reason,
        )

        vt_orderid = self._main_engine.send_order(req, GATEWAY_NAME)
        if not vt_orderid:
            logger.error("[VNPyBridge] send_order 返回空 orderid")
            return None

        # 同步等待成交
        pending = PendingOrder(orderid=vt_orderid, event=threading.Event())
        with self._order_lock:
            self._pending_orders[vt_orderid] = pending

        # 再推一次 tick 确保撮合
        time.sleep(0.02)
        self._send_tick(symbol, price)

        filled = pending.event.wait(timeout=5.0)

        with self._order_lock:
            self._pending_orders.pop(vt_orderid, None)

        if not filled:
            logger.warning("[VNPyBridge] 订单 %s 超时未成交", vt_orderid)
            return None

        if pending.result and pending.result.get("status") == "ALLTRADED":
            logger.info(
                "[VNPyBridge] 成交: %s %s %.2f x %d",
                symbol, direction, price, volume,
            )
            return vt_orderid

        return None

    # ── 事件回调 (内部) ────────────────────────────────────────

    def _on_trade_event(self, event: Event) -> None:
        """成交事件回调 — 唤醒等待线程"""
        trade: TradeData = event.data
        # VN.PY 的 trade.orderid 不含网关前缀 (如 "260725..."),
        # 而 pending_orders 的 key 是 vt_orderid (如 "PAPER.260725...")
        vt_orderid = f"{GATEWAY_NAME}.{trade.orderid}"
        with self._order_lock:
            pending = self._pending_orders.get(vt_orderid)
            if pending:
                pending.result = {"status": "ALLTRADED"}
                pending.event.set()

    def _on_order_event(self, event: Event) -> None:
        """订单事件回调 — 处理拒单"""
        order: OrderData = event.data
        if order.status == Status.REJECTED:
            with self._order_lock:
                # 同时尝试 vt_orderid 和纯 orderid
                pending = self._pending_orders.get(order.vt_orderid)
                if pending:
                    pending.result = {
                        "status": "REJECTED",
                        "reason": "Order rejected by paper engine",
                    }
                    pending.event.set()

    # ── 账户/持仓查询 ──────────────────────────────────────────

    def _get_vnpy_positions(self) -> list:
        """获取 VN.PY 当前持仓 (从 PaperEngine 内部 positions dict)"""
        if self._paper_engine is None:
            return []
        result = []
        for pos_key, pos in self._paper_engine.positions.items():
            if pos.volume <= 0:
                continue
            result.append(pos)
        return result

    # ── 行情辅助 ──────────────────────────────────────────────

    @staticmethod
    def _vt_to_marcus_symbol(vt_symbol: str) -> str:
        """VN.PY vt_symbol (如 600900.SSE) → Marcus 代码 (SH600900)"""
        parts = vt_symbol.rsplit(".", 1)
        code = parts[0]
        ex_str = parts[1] if len(parts) == 2 else ""
        prefix = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}.get(ex_str, "")
        return f"{prefix}{code}"

    @staticmethod
    def _get_workspace() -> Path:
        """获取项目根目录"""
        ws = os.getenv("MARCUS_WORKSPACE")
        if ws:
            return Path(ws)
        return Path(__file__).parents[4]

    def _fetch_live_quotes(self, marcus_symbols: List[str]) -> Dict[str, dict]:
        """获取实时行情 (腾讯 qt.gtimg.cn, 通过 XueqiuEngine)"""
        if not marcus_symbols:
            return {}
        try:
            workspace = VNPyBridge._get_workspace()
            core_dir = workspace / "core"
            config_file = core_dir / "config.json"
            if not config_file.exists():
                logger.warning("[VNPyBridge] xueqiu config.json not found at %s", config_file)
                return {}
            sys.path.insert(0, str(core_dir))
            from xueqiu_engine import XueqiuEngine
            engine = XueqiuEngine(config_file=str(config_file))
            quotes = engine.get_stock_quotes(marcus_symbols)
            result = {}
            for sym, q in (quotes or {}).items():
                if q and q.get("current"):
                    result[sym] = {
                        "price": q.get("current"),
                        "change_pct": q.get("percent", 0) or 0,
                        "last_close": q.get("last_close", 0) or 0,
                    }
            return result
        except Exception as e:
            logger.warning("[VNPyBridge] 获取实时行情失败: %s", e)
            return {}

    def get_account(self) -> dict:
        """
        获取账户摘要（按市价估值）。
        优先从 VN.PY 内存查, 回退到 PostgreSQL。
        """
        if self._main_engine is None:
            return self._get_account_from_pg()

        accounts = self._main_engine.get_all_accounts()
        if accounts:
            acct = accounts[0]
            balance = acct.balance
            frozen = acct.frozen
        else:
            # Paper engine 的 account 可能没推送 — 自己算
            balance = self._available_cash
            frozen = self._frozen_cash

        positions = self._get_vnpy_positions()

        # 获取实时行情, 按市价计算持仓市值和浮动盈亏
        marcus_symbols = [
            self._vt_to_marcus_symbol(
                p.vt_symbol if hasattr(p, "vt_symbol") else p.symbol
            )
            for p in positions
        ]
        quotes = self._fetch_live_quotes(marcus_symbols)

        position_value = 0.0  # 市价估值
        position_cost = 0.0   # 成本价
        for p in positions:
            vt_symbol = p.vt_symbol if hasattr(p, "vt_symbol") else p.symbol
            marcus_sym = self._vt_to_marcus_symbol(vt_symbol)
            quote = quotes.get(marcus_sym, {})
            current_price = quote.get("price", p.price)
            position_value += p.volume * current_price
            position_cost += p.volume * p.price

        total_asset = balance + frozen + position_value
        total_pnl = total_asset - self._initial_capital

        # 从 paper_trades 汇总已实现盈亏
        realized_pnl = self._get_realized_pnl_from_pg()

        # 浮动盈亏 = 市价估值 - 成本价 (更准确的 mark-to-market)
        float_pnl = position_value - position_cost

        return {
            "initial_capital": self._initial_capital,
            "available_cash": balance,
            "frozen_cash": frozen,
            "position_value": position_value,
            "position_cost": position_cost,
            "total_asset": total_asset,
            "realized_pnl": realized_pnl,
            "float_pnl": float_pnl,
            "total_pnl": total_pnl,
            "position_count": len(positions),
        }

    def get_positions(self) -> List[dict]:
        """获取当前所有净持仓 (从 PaperEngine 内部, 包含市价)"""
        if self._main_engine is None:
            return self._get_positions_from_pg()

        vnpy_positions = self._get_vnpy_positions()

        # 获取实时行情
        marcus_symbols = [
            self._vt_to_marcus_symbol(
                p.vt_symbol if hasattr(p, "vt_symbol") else p.symbol
            )
            for p in vnpy_positions
        ]
        quotes = self._fetch_live_quotes(marcus_symbols)

        result = []
        for p in vnpy_positions:
            vt_symbol = p.vt_symbol if hasattr(p, "vt_symbol") else p.symbol
            marcus_sym = self._vt_to_marcus_symbol(vt_symbol)
            quote = quotes.get(marcus_sym, {})
            current_price = quote.get("price", p.price)
            change_pct = quote.get("change_pct", 0.0)
            market_value = int(p.volume) * current_price
            cost_value = int(p.volume) * p.price
            result.append({
                "symbol": marcus_sym,
                "volume": int(p.volume),
                "frozen": int(p.frozen),
                "avg_price": p.price,
                "current_price": current_price,
                "change_pct": change_pct,
                "market_value": market_value,
                "float_pnl": market_value - cost_value,
                "highest_price": max(p.price, current_price),
                "entry_date": "",
            })
        return result

    def get_trades(self, symbol: str = None, limit: int = 100) -> List[dict]:
        """查询成交记录 (从 PostgreSQL)"""
        return self._query_trades_from_pg(symbol, limit)

    def get_orders(self, symbol: str = None, status: str = None, limit: int = 100) -> List[dict]:
        """查询订单记录 (从 PostgreSQL)"""
        return self._query_orders_from_pg(symbol, status, limit)

    # ── PostgreSQL 数据访问 ────────────────────────────────────

    @staticmethod
    def _parse_db_url(url: str) -> dict:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return {
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 5432,
            "dbname": (parsed.path or "/marcus_trading").lstrip("/"),
            "user": parsed.username or "marcus",
            "password": parsed.password or "marcus123",
        }

    def _get_pg_conn(self):
        pg_params = self._parse_db_url(self._db_url)
        conn = psycopg2.connect(**pg_params)
        conn.autocommit = True
        return conn

    def _load_account_from_pg(self) -> None:
        """从 PostgreSQL 加载账户现金状态（account_id='stock'）。

        账本（paper_account_info）缺失时回退到注册表（paper_accounts.initial_capital），
        保证与 calc_position/portfolio 使用同一资金口径，避免执行网关落回 10w 默认值误拒。
        """
        try:
            conn = self._get_pg_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT initial_capital, available_cash, frozen_cash "
                "FROM paper_account_info WHERE account_id = 'stock'"
            )
            row = cur.fetchone()
            if not row:
                # 口径兜底：注册表为准
                cur.execute(
                    "SELECT initial_capital FROM paper_accounts WHERE account_id = 'stock'"
                )
                reg = cur.fetchone()
                conn.close()
                if reg:
                    self._initial_capital = float(reg[0])
                    self._available_cash = float(reg[0])
                    self._frozen_cash = 0.0
                return
            conn.close()
            self._initial_capital = float(row[0])
            self._available_cash = float(row[1])
            self._frozen_cash = float(row[2] or 0)
        except Exception as e:
            logger.warning("[VNPyBridge] 从 PG 加载账户失败: %s", e)

    def _clear_paper_data_files(self) -> None:
        """清理 VN.PY paper account 持久化文件, 并注入已有持仓"""

        pg_positions = self._get_positions_from_pg()
        data_dir = Path.home() / ".vntrader"
        data_dir.mkdir(exist_ok=True)

        # 写入持仓数据 (VN.PY 格式: vt_symbol, volume, price, direction)
        position_data = []
        for pos in pg_positions:
            if pos["volume"] <= 0:
                continue
            vt_symbol = self._make_vt_symbol(pos["symbol"])
            position_data.append({
                "vt_symbol": vt_symbol,
                "volume": int(pos["volume"]),
                "price": float(pos["avg_price"]),
                "direction": Direction.NET.value,  # vn.py Direction 枚举值（NET='Net'），中文会导致 load_data 抛 '净' is not a valid Direction
            })
        data_file = data_dir / "paper_account_data.json"
        with open(data_file, "w") as f:
            json.dump(position_data, f)
        logger.info(
            "[VNPyBridge] 已写入 %d 条种子持仓", len(position_data)
        )

        # 写入设置
        setting_file = data_dir / "paper_account_setting.json"
        with open(setting_file, "w") as f:
            json.dump({
                "trade_slippage": 0,
                "timer_interval": 3,
                "instant_trade": True,
            }, f)

    def _get_account_from_pg(self) -> dict:
        """从 PostgreSQL 查账户 (VN.PY 未启动时的回退)"""
        try:
            conn = self._get_pg_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT initial_capital, available_cash, frozen_cash "
                "FROM paper_account_info WHERE account_id = 'stock'"
            )
            row = cur.fetchone()
            conn.close()
            if row:
                return {
                    "initial_capital": float(row[0]),
                    "available_cash": float(row[1]),
                    "frozen_cash": float(row[2] or 0),
                    "position_value": 0,
                    "total_asset": float(row[1]),
                    "realized_pnl": 0,
                    "float_pnl": 0,
                    "total_pnl": float(row[1]) - float(row[0]),
                    "position_count": 0,
                }
        except Exception as e:
            logger.error("[VNPyBridge] PG 查询账户失败: %s", e)
        return {}

    def _get_positions_from_pg(self) -> List[dict]:
        """从 PostgreSQL 查持仓"""
        try:
            conn = self._get_pg_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT symbol, volume, frozen, avg_price, entry_date, highest_price "
                "FROM paper_positions WHERE account_id = 'stock' "
                "AND volume > 0 AND coalesce(frozen, 0) >= 0"
            )
            result = []
            for row in cur.fetchall():
                result.append({
                    "symbol": row[0],
                    "volume": int(row[1] or 0),
                    "frozen": int(row[2] or 0),
                    "avg_price": float(row[3] or 0),
                    "entry_date": row[4] or "",
                    "highest_price": float(row[5] or 0),
                })
            conn.close()
            return result
        except Exception:
            return []

    def _get_realized_pnl_from_pg(self) -> float:
        """从 PostgreSQL 汇总已实现盈亏"""
        try:
            conn = self._get_pg_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(SUM(profit), 0) FROM paper_trades "
                "WHERE account_id = 'stock' "
                "AND (voided = 0 OR voided IS NULL) AND direction = '卖出'"
            )
            row = cur.fetchone()
            conn.close()
            return float(row[0]) if row else 0.0
        except Exception:
            return 0.0

    def _query_trades_from_pg(self, symbol: str = None, limit: int = 100) -> List[dict]:
        """从 PostgreSQL 查成交"""
        try:
            conn = self._get_pg_conn()
            cur = conn.cursor()
            if symbol:
                cur.execute(
                    "SELECT id, orderid, symbol, direction, price, volume, amount, "
                    "profit, created_at, trade_date, reason "
                    "FROM paper_trades WHERE account_id = 'stock' "
                    "AND (voided = 0 OR voided IS NULL) "
                    "AND symbol = %s ORDER BY id DESC LIMIT %s",
                    (symbol, limit),
                )
            else:
                cur.execute(
                    "SELECT id, orderid, symbol, direction, price, volume, amount, "
                    "profit, created_at, trade_date, reason "
                    "FROM paper_trades WHERE account_id = 'stock' "
                    "AND (voided = 0 OR voided IS NULL) "
                    "ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
            cols = [d[0] for d in cur.description]
            result = [dict(zip(cols, row)) for row in cur.fetchall()]
            conn.close()
            return result
        except Exception:
            return []

    def _query_orders_from_pg(self, symbol: str = None, status: str = None, limit: int = 100) -> List[dict]:
        """从 PostgreSQL 查订单"""
        try:
            conn = self._get_pg_conn()
            cur = conn.cursor()
            sql = (
                "SELECT orderid, symbol, direction, price, volume, status, "
                "traded, created_at, updated_at, reason "
                "FROM paper_orders WHERE account_id = 'stock'"
            )
            params = []
            if symbol:
                sql += " AND symbol = %s"
                params.append(symbol)
            if status:
                sql += " AND status = %s"
                params.append(status)
            sql += " ORDER BY created_at DESC LIMIT %s"
            params.append(limit)
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            result = [dict(zip(cols, row)) for row in cur.fetchall()]
            conn.close()
            return result
        except Exception:
            return []


# ── 工厂函数 ──────────────────────────────────────────────────

def get_bridge(db_url: str = None) -> Optional["VNPyBridge"]:
    """
    根据 ENGINE_BACKEND 环境变量返回对应的交易后端。

    Returns:
        VNPyBridge (ENGINE_BACKEND=vnpy) 或 None (ENGINE_BACKEND=paper,
        使用 legacy PaperTradingEngine)
    """
    import os
    backend = os.getenv("ENGINE_BACKEND", "vnpy")
    if backend == "vnpy":
        return VNPyBridge(db_url=db_url)
    return None
