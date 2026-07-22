#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VN.PY 模拟交易引擎 - 持久化版本 (PostgreSQL)

支持：
- PostgreSQL 数据库存储交易记录
- JSON 文件存储账户状态（已废弃，仅用于首次迁移）
- 查询历史交易和持仓
- SELECT ... FOR UPDATE 行锁保证并发资金安全
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
from urllib.parse import urlparse

import psycopg2
import psycopg2.extensions

# Use workspace_detector for cross-platform path resolution
sys.path.insert(0, str(Path(__file__).parent.parent))
from workspace_detector import get_xueqiu_dir

class Direction(Enum):
    LONG = "买入"
    SHORT = "卖出"


class OrderStatus(Enum):
    SUBMITTING = "提交中"
    NOTTRADED = "未成交"
    PARTTRADED = "部分成交"
    ALLTRADED = "全部成交"
    CANCELLED = "已撤销"
    REJECTED = "拒单"


@dataclass
class Order:
    orderid: str
    symbol: str
    direction: str
    price: float
    volume: int
    status: str = "提交中"
    traded: int = 0
    created_at: str = None
    updated_at: str = None
    reason: str = ""
    
    def __post_init__(self):
        now = datetime.now().isoformat()
        if self.created_at is None:
            self.created_at = now
        self.updated_at = now


@dataclass
class Position:
    symbol: str
    name: str = ""  # 股票名称，与代码一起存储避免匹配错误
    volume: int = 0
    avg_price: float = 0.0
    frozen: int = 0
    highest_price: float = 0.0  # 持仓期间最高价
    entry_date: str = ""  # 入场日期


class PaperTradingEngine:
    """
    持久化模拟交易引擎

    数据存储（PostgreSQL）：
    - 账户状态：paper_account_info 表
    - 持仓追踪：paper_positions 表
    - 交易记录：paper_trades 表
    - 订单记录：paper_orders 表
    - 每日快照：paper_daily_snapshot 表
    """

    @staticmethod
    def _parse_db_url(url: str = None):
        """从 DATABASE_URL 解析 PostgreSQL 连接参数。"""
        if url is None:
            url = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@localhost:5432/marcus_trading")
        parsed = urlparse(url)
        return {
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 5432,
            "dbname": (parsed.path or "/marcus_trading").lstrip("/"),
            "user": parsed.username or "marcus",
            "password": parsed.password or "marcus123",
        }

    def _get_pg_conn(self):
        """获取 PostgreSQL 连接（autocommit=False，显式事务控制）。"""
        conn = psycopg2.connect(**self._pg_params)
        conn.autocommit = False
        return conn
    
    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """规范化股票代码，自动添加 SH/SZ 前缀

        A股前缀规则：
        - 000xxx ~ 004xxx → SZ (深市主板)
        - 300xxx ~ 301xxx → SZ (创业板)
        - 600xxx ~ 605xxx → SH (沪市主板)
        - 688xxx → SH (科创板)

        输入格式兼容：
        - "300162" / "SZ300162" / "300162.SZ" → 统一输出 "SZ300162"
        - "600519" / "SH600519" / "600519.SH" → 统一输出 "SH600519"

        Args:
            symbol: 原始代码，如 "300162" 或 "SZ300162" 或 "300162.SZ"

        Returns:
            带前缀的规范代码，如 "SZ300162"
        """
        symbol = symbol.strip().upper()
        # ── 处理 Tushare/通达信 code.exchange 格式 "301566.SZ" → "SZ301566" ──
        if '.' in symbol:
            code, exchange = symbol.split('.', 1)
            if code.isdigit() and len(code) == 6 and exchange in ('SH', 'SZ', 'BJ'):
                return f"{exchange}{code}"
        # 已有前缀，直接返回
        if symbol.startswith(("SH", "SZ", "BJ")):
            return symbol
        # 纯数字代码，自动补充前缀
        if symbol.isdigit() and len(symbol) == 6:
            prefix = symbol[:3]
            if "000" <= prefix <= "004":
                return f"SZ{symbol}"
            elif "300" <= prefix <= "301":
                return f"SZ{symbol}"
            elif "600" <= prefix <= "605":
                return f"SH{symbol}"
            elif prefix == "688":
                return f"SH{symbol}"
        # 无法识别，保持原样（可能是期货代码等）
        return symbol

    def __init__(self, data_dir: str = "./data", initial_capital: float = 1000000.0):
        """
        初始化模拟账户

        Args:
            data_dir: 数据目录（兼容旧接口，PostgreSQL 模式下仅用于 account.json 回退）
            initial_capital: 初始资金（仅首次创建账户时有效）
        """
        self.data_dir = os.path.expanduser(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.db_file = os.path.join(self.data_dir, "trades.db")  # 保留用于迁移

        # PostgreSQL 连接参数
        self._pg_params = self._parse_db_url()

        # 初始化数据库（必须先执行，确保表存在）
        self._init_database()
        # 回测专用: 当前模拟交易日, FIFO排序用; 实盘为 None 则回退 created_at
        self._trade_date: Optional[str] = None

        # 初始化账户
        self._init_account(initial_capital)
    
    def _get_conn(self):
        """获取 PostgreSQL 连接（兼容旧接口名，内部调用 _get_pg_conn）。"""
        return self._get_pg_conn()

    def _init_account(self, initial_capital: float):
        """初始化或加载账户 - 优先从 PostgreSQL paper_account_info 表读取"""
        try:
            conn = self._get_pg_conn()
            cursor = conn.cursor()
            cursor.execute(
                'SELECT initial_capital, available_cash, frozen_cash, order_counter '
                'FROM paper_account_info WHERE id=1'
            )
            row = cursor.fetchone()
            conn.close()

            if row:
                self.initial_capital = row[0]
                self.available_cash = row[1]
                self.frozen_cash = row[2] if row[2] is not None else 0.0
                self.order_counter = row[3] if row[3] is not None else 0
                self.positions = self._load_positions_from_db()
                print(f"[OK] 账户数据从 PostgreSQL 读取，可用资金：{self.available_cash:,.2f}")
                print(f"[OK] 持仓从 PostgreSQL 读取: {len(self.positions)} 只")
                return
        except Exception as e:
            print(f"[迁移] 从 PostgreSQL 读取账户失败: {e}，尝试从 account.json 迁移...")

        # 降级：从 account.json 读取（首次迁移）
        if os.path.exists(os.path.join(self.data_dir, "account.json")):
            with open(os.path.join(self.data_dir, "account.json"), 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.initial_capital = data.get('initial_capital', initial_capital)
                self.available_cash = data.get('available_cash', initial_capital)
                self.frozen_cash = data.get('frozen_cash', 0.0)
                self.order_counter = data.get('order_counter', 0)

            self._save_account()
            self.positions = self._load_positions_from_db()

            print(f"[OK] 账户数据从 account.json 迁移至 PostgreSQL，可用资金：{self.available_cash:,.2f}")
            print(f"[OK] 持仓从 PostgreSQL 读取: {len(self.positions)} 只")
        else:
            # 创建新账户
            self.initial_capital = initial_capital
            self.available_cash = initial_capital
            self.frozen_cash = 0.0
            self.positions = {}
            self.order_counter = 0
            self._save_account()
            print(f"[OK] 已创建新账户，初始资金：{initial_capital:,.2f}")
    
    def _load_positions_from_db(self) -> dict:
        """从 PostgreSQL 读取持仓（FIFO 计算）"""
        positions = {}

        try:
            conn = self._get_pg_conn()
            cursor = conn.cursor()

            # 从 paper_positions 表读取 entry_date 和 highest_price
            pos_meta = {}
            cursor.execute('SELECT symbol, entry_date, highest_price FROM paper_positions')
            for row in cursor.fetchall():
                pos_meta[row[0]] = {'entry_date': row[1], 'highest_price': float(row[2] or 0)}

            # 获取所有交易记录（与 portfolio.calculate_positions_from_db 保持一致）
            cursor.execute(
                'SELECT symbol, direction, price, volume FROM paper_trades '
                'WHERE voided = 0 OR voided IS NULL '
                'ORDER BY COALESCE(trade_date, created_at::date::text), id'
            )
            trades = cursor.fetchall()

            # FIFO 计算持仓
            for symbol, direction, price, vol in trades:
                if symbol not in positions:
                    positions[symbol] = []

                if direction == '买入':
                    positions[symbol].append({'price': price, 'volume': vol})
                else:  # 卖出
                    remaining = vol
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

            # 转换为 Position 对象（含 entry_date/highest_price）
            result = {}
            for symbol, lots in positions.items():
                total_vol = sum(lot['volume'] for lot in lots)
                if total_vol > 0:
                    total_cost = sum(lot['price'] * lot['volume'] for lot in lots)
                    avg_price = total_cost / total_vol
                    meta = pos_meta.get(symbol, {})
                    result[symbol] = Position(
                        symbol=symbol,
                        volume=total_vol,
                        avg_price=avg_price,
                        frozen=0,
                        entry_date=meta.get('entry_date', ''),
                        highest_price=meta.get('highest_price', avg_price)
                    )

            conn.close()
            return result
        except Exception as e:
            print(f"[警告] 从 PostgreSQL 读取持仓失败: {e}")
            return {}

    def update_position_meta(self, symbol: str, entry_date: str = None, highest_price: float = None):
        """更新持仓元数据到 paper_positions 表"""
        if entry_date is None and highest_price is None:
            return

        conn = self._get_pg_conn()
        cursor = conn.cursor()
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        cursor.execute('SELECT highest_price FROM paper_positions WHERE symbol = %s', (symbol,))
        row = cursor.fetchone()

        if row:
            current_high = float(row[0] or 0)
            new_high = highest_price if (highest_price is not None and highest_price > current_high) else current_high

            if entry_date is not None:
                cursor.execute(
                    'UPDATE paper_positions SET entry_date = %s, highest_price = %s, updated_at = %s WHERE symbol = %s',
                    (entry_date, new_high, now, symbol)
                )
            else:
                cursor.execute(
                    'UPDATE paper_positions SET highest_price = %s, updated_at = %s WHERE symbol = %s',
                    (new_high, now, symbol)
                )
        else:
            cursor.execute(
                'INSERT INTO paper_positions (symbol, entry_date, highest_price, updated_at) VALUES (%s, %s, %s, %s)',
                (symbol, entry_date or datetime.now().strftime('%Y-%m-%d'),
                 highest_price if highest_price is not None else 0.0, now)
            )

        conn.commit()
        conn.close()

    def remove_position_meta(self, symbol: str):
        """从 paper_positions 表删除持仓记录（卖出后调用）"""
        try:
            conn = self._get_pg_conn()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM paper_positions WHERE symbol = %s', (symbol,))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[警告] 删除持仓元数据失败: {e}")

    def _save_account(self):
        """保存账户状态到 PostgreSQL paper_account_info（SELECT ... FOR UPDATE 防并发写）"""
        try:
            conn = self._get_pg_conn()
            cursor = conn.cursor()
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            # 锁定行再 upsert，保证并发安全
            cursor.execute('SELECT id FROM paper_account_info WHERE id = 1 FOR UPDATE')
            if cursor.fetchone():
                cursor.execute(
                    'UPDATE paper_account_info SET initial_capital = %s, available_cash = %s, '
                    'frozen_cash = %s, order_counter = %s, updated_at = %s WHERE id = 1',
                    (self.initial_capital, self.available_cash, self.frozen_cash, self.order_counter, now)
                )
            else:
                cursor.execute(
                    'INSERT INTO paper_account_info (id, initial_capital, available_cash, frozen_cash, order_counter, updated_at) '
                    'VALUES (1, %s, %s, %s, %s, %s)',
                    (self.initial_capital, self.available_cash, self.frozen_cash, self.order_counter, now)
                )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[警告] 保存账户状态失败: {e}")

    def _init_database(self):
        """初始化 PostgreSQL 表结构"""
        conn = self._get_pg_conn()
        cursor = conn.cursor()

        # PostgreSQL DDL — 5 张表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS paper_orders (
                orderid TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                price DOUBLE PRECISION NOT NULL,
                volume INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT '提交中',
                traded INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                reason TEXT DEFAULT ''
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS paper_trades (
                id SERIAL PRIMARY KEY,
                orderid TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                price DOUBLE PRECISION NOT NULL,
                volume INTEGER NOT NULL,
                amount DOUBLE PRECISION NOT NULL,
                profit DOUBLE PRECISION DEFAULT 0,
                created_at TEXT NOT NULL,
                trade_date TEXT,
                voided INTEGER DEFAULT 0,
                void_reason TEXT,
                voided_at TEXT,
                reason TEXT DEFAULT ''
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS paper_positions (
                symbol TEXT PRIMARY KEY,
                entry_date TEXT NOT NULL,
                highest_price DOUBLE PRECISION DEFAULT 0.0,
                updated_at TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS paper_account_info (
                id INTEGER PRIMARY KEY,
                initial_capital DOUBLE PRECISION NOT NULL,
                available_cash DOUBLE PRECISION NOT NULL,
                frozen_cash DOUBLE PRECISION NOT NULL DEFAULT 0,
                order_counter INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS paper_daily_snapshot (
                trade_date TEXT PRIMARY KEY,
                total_asset DOUBLE PRECISION NOT NULL,
                available_cash DOUBLE PRECISION NOT NULL,
                frozen_cash DOUBLE PRECISION DEFAULT 0,
                position_value DOUBLE PRECISION DEFAULT 0,
                cost_value DOUBLE PRECISION DEFAULT 0,
                realized_pnl DOUBLE PRECISION DEFAULT 0,
                float_pnl DOUBLE PRECISION DEFAULT 0,
                total_pnl DOUBLE PRECISION DEFAULT 0,
                initial_capital DOUBLE PRECISION NOT NULL,
                created_at TEXT NOT NULL
            )
        ''')

        # 索引
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol ON paper_trades (symbol)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_paper_trades_time ON paper_trades (created_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_paper_trades_dir_date ON paper_trades (direction, created_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_paper_orders_symbol ON paper_orders (symbol)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_paper_orders_status ON paper_orders (status)')

        conn.commit()
        conn.close()
        print(f"[OK] PostgreSQL 表已初始化")

    
    def _get_stock_name(self, symbol: str) -> str:
        """
        获取股票名称（从雪球 API）
        
        Args:
            symbol: 股票代码 (如 SH600519)
            
        Returns:
            股票名称，获取失败返回空字符串
        """
        try:
            xueqiu_dir = str(get_xueqiu_dir())
            xueqiu_config = os.path.join(xueqiu_dir, 'config.json')
            
            if os.path.exists(xueqiu_config):
                sys.path.insert(0, xueqiu_dir)
                from xueqiu_engine import XueqiuEngine
                engine = XueqiuEngine(config_file=xueqiu_config)
                quote = engine.get_stock_quote(symbol)
                name = quote.get('name', '')
                if name:
                    print(f"[OK] 获取股票名称：{symbol} = {name}")
                    return name
        except Exception as e:
            print(f"[WARN]️ 获取 {symbol} 名称失败：{e}")
        return ""
    
    def buy(self, symbol: str, price: float, volume: int, reason: str = "") -> Optional[str]:
        """
        买入股票/期货
        
        Args:
            symbol: 标的代码
            price: 买入价格
            volume: 买入数量
            
        Returns:
            订单 ID，失败返回 None
        """
        # 规范化股票代码（自动补充 SH/SZ 前缀）
        symbol = self._normalize_symbol(symbol)

        # 计算所需资金
        if symbol.startswith("SH") or symbol.startswith("SZ"):
            required_cash = price * volume * 1.0005  # 含手续费
        else:
            required_cash = price * volume * 100 * 0.1  # 期货保证金
        
        if required_cash > self.available_cash:
            print(f"[ERR] 资金不足！需要 {required_cash:.2f}, 可用 {self.available_cash:.2f}")
            return None
        
        # 获取股票名称（A 股）
        stock_name = ""
        if symbol.startswith("SH") or symbol.startswith("SZ"):
            stock_name = self._get_stock_name(symbol)
        
        # 冻结资金
        self.available_cash -= required_cash
        self.frozen_cash += required_cash
        
        # 从数据库获取最大订单ID，确保同步
        try:
            conn = self._get_pg_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(orderid) FROM paper_orders WHERE orderid LIKE 'ORD%'")
            result = cursor.fetchone()[0]
            conn.close()
            if result:
                max_id = int(result.replace('ORD', ''))
                if self.order_counter <= max_id:
                    self.order_counter = max_id + 1
        except:
            pass
        
        # 创建订单
        self.order_counter += 1
        order_id = f"ORD{self.order_counter:06d}"
        order = Order(
            orderid=order_id,
            symbol=symbol,
            direction=Direction.LONG.value,
            price=price,
            volume=volume,
            status=OrderStatus.SUBMITTING.value,
            reason=reason
        )
        
        # 保存订单到数据库
        self._save_order(order)
        
        # 保存账户状态
        self._save_account()
        
        name_display = f"({stock_name})" if stock_name else ""
        print(f"[UP] 买入委托：{symbol} {name_display} @ {price:.2f} x {volume} | 订单号：{order_id}")
        return order_id
    
    def sell(self, symbol: str, price: float, volume: int, reason: str = "") -> Optional[str]:
        """
        卖出股票/期货
        
        Args:
            symbol: 标的代码
            price: 卖出价格
            volume: 卖出数量
            
        Returns:
            订单 ID，失败返回 None
        """
        # 规范化股票代码（自动补充 SH/SZ 前缀）
        symbol = self._normalize_symbol(symbol)

        if symbol not in self.positions:
            print(f"[ERR] 没有 {symbol} 的持仓")
            return None
        
        pos = self.positions[symbol]
        if pos.volume - pos.frozen < volume:
            print(f"[ERR] 持仓不足！可用 {pos.volume - pos.frozen}, 欲卖 {volume}")
            return None
        
        pos.frozen += volume
        
        # 从数据库获取最大订单ID，确保同步
        try:
            conn = self._get_pg_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(orderid) FROM paper_orders WHERE orderid LIKE 'ORD%'")
            result = cursor.fetchone()[0]
            conn.close()
            if result:
                max_id = int(result.replace('ORD', ''))
                if self.order_counter <= max_id:
                    self.order_counter = max_id + 1
        except:
            pass
        
        self.order_counter += 1
        order_id = f"ORD{self.order_counter:06d}"
        order = Order(
            orderid=order_id,
            symbol=symbol,
            direction=Direction.SHORT.value,
            price=price,
            volume=volume,
            status=OrderStatus.SUBMITTING.value,
            reason=reason
        )
        
        self._save_order(order)
        self._save_account()
        
        print(f"[DOWN] 卖出委托：{symbol} @ {price:.2f} x {volume} | 订单号：{order_id}")
        return order_id
    
    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        conn = self._get_pg_conn()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM paper_orders WHERE orderid = %s', (order_id,))
        row = cursor.fetchone()

        if not row:
            print(f"[ERR] 订单 {order_id} 不存在")
            conn.close()
            return False

        status = row[5]
        if status in [OrderStatus.ALLTRADED.value, OrderStatus.CANCELLED.value, OrderStatus.REJECTED.value]:
            print(f"[ERR] 订单状态为 {status}, 无法撤销")
            conn.close()
            return False

        # 更新订单状态
        cursor.execute(
            'UPDATE paper_orders SET status = %s, updated_at = %s WHERE orderid = %s',
            (OrderStatus.CANCELLED.value, datetime.now().isoformat(), order_id)
        )
        
        conn.commit()
        conn.close()
        
        # 解冻资金/持仓（区分买卖方向）
        direction = row[2]
        if direction == Direction.LONG.value:
            # 买入订单：解冻资金（需与 buy() 冻结金额一致，A股含手续费）
            if row[1].startswith("SH") or row[1].startswith("SZ"):
                frozen_amount = row[3] * row[4] * 1.0005
            else:
                frozen_amount = row[3] * row[4] * 100 * 0.1
            self.frozen_cash -= frozen_amount
            self.available_cash += frozen_amount
        else:
            # 卖出订单：解冻持仓（sell() 只冻结了 pos.frozen，不涉及现金）
            symbol = row[1]
            if symbol in self.positions:
                self.positions[symbol].frozen -= row[4]
        self._save_account()
        
        print(f"[OK] 已撤销订单：{order_id}")
        return True
    
    def match_order(self, order_id: str, fill_price: float) -> bool:
        """
        模拟订单成交（PostgreSQL 事务 + FOR UPDATE 行锁）

        Args:
            order_id: 订单 ID
            fill_price: 成交价格

        Returns:
            是否成功
        """
        conn = self._get_pg_conn()
        cursor = conn.cursor()

        # 锁定账户行，保证整个 match_order 期间无并发写
        cursor.execute('SELECT id FROM paper_account_info WHERE id = 1 FOR UPDATE')

        # 获取订单
        cursor.execute('SELECT * FROM paper_orders WHERE orderid = %s', (order_id,))
        row = cursor.fetchone()

        if not row:
            print(f"[ERR] 订单 {order_id} 不存在")
            conn.rollback()
            conn.close()
            return False

        order = Order(
            orderid=row[0], symbol=row[1], direction=row[2],
            price=row[3], volume=row[4], status=row[5],
            traded=row[6], created_at=row[7], updated_at=row[8],
            reason=row[9] if len(row) > 9 else ''
        )

        # 更新订单状态
        order.status = OrderStatus.ALLTRADED.value
        order.traded = order.volume
        order.updated_at = datetime.now().isoformat()

        cursor.execute(
            'UPDATE paper_orders SET status = %s, traded = %s, updated_at = %s WHERE orderid = %s',
            (order.status, order.traded, order.updated_at, order_id)
        )
        
        # 更新持仓
        if order.symbol not in self.positions:
            if order.direction == Direction.LONG.value:
                # 买入: 首次建仓
                stock_name = ""
                if order.symbol.startswith("SH") or order.symbol.startswith("SZ"):
                    stock_name = self._get_stock_name(order.symbol)
                today = datetime.now().strftime('%Y-%m-%d')
                self.positions[order.symbol] = Position(symbol=order.symbol, name=stock_name, entry_date=today)
            else:
                print(f"[ERR] 无法卖出 {order.symbol}: 无持仓记录 (可能已被同日早前卖出清仓)")
                conn.rollback()
                conn.close()
                return False
        
        pos = self.positions[order.symbol]
        
        if order.direction == Direction.LONG.value:
            # 买入成交
            total_cost = pos.avg_price * pos.volume + fill_price * order.volume
            pos.volume += order.volume
            pos.avg_price = total_cost / pos.volume if pos.volume > 0 else 0
            # 解冻资金（与 buy() 中冻结的金额一致：含手续费）
            frozen_amount = order.price * order.volume
            if order.symbol.startswith("SH") or order.symbol.startswith("SZ"):
                frozen_amount = order.price * order.volume * 1.0005  # A股含手续费
            self.frozen_cash -= frozen_amount

            # Step 8: 更新持仓元数据到 paper_positions 表
            if not pos.entry_date:
                pos.entry_date = datetime.now().strftime('%Y-%m-%d')
            now_meta = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute('SELECT highest_price FROM paper_positions WHERE symbol = %s', (order.symbol,))
            meta_row = cursor.fetchone()
            if meta_row:
                current_high = float(meta_row[0] or 0)
                new_high = fill_price if fill_price > current_high else current_high
                cursor.execute(
                    'UPDATE paper_positions SET entry_date = %s, highest_price = %s, updated_at = %s WHERE symbol = %s',
                    (pos.entry_date, new_high, now_meta, order.symbol)
                )
            else:
                cursor.execute(
                    'INSERT INTO paper_positions (symbol, entry_date, highest_price, updated_at) VALUES (%s, %s, %s, %s)',
                    (order.symbol, pos.entry_date, fill_price, now_meta)
                )

            # 记录成交
            td = self._trade_date or datetime.now().strftime('%Y-%m-%d')
            cursor.execute(
                'INSERT INTO paper_trades (orderid, symbol, direction, price, volume, amount, profit, created_at, trade_date, reason) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                (order_id, order.symbol, order.direction, fill_price, order.volume,
                 fill_price * order.volume, 0, datetime.now().isoformat(), td, getattr(order, 'reason', ''))
            )

        else:
            # 卖出成交（FIFO 修正）
            date_col = "trade_date" if self._trade_date else "created_at"
            date_val = self._trade_date or (order.created_at if hasattr(order, 'created_at') and order.created_at else datetime.now().isoformat())

            cursor.execute(
                'SELECT price, volume FROM paper_trades WHERE symbol = %s AND direction = %s '
                'AND (voided = 0 OR voided IS NULL) '
                'ORDER BY COALESCE(trade_date, created_at::date::text), id',
                (order.symbol, Direction.LONG.value)
            )
            buy_trades = cursor.fetchall()
            cursor.execute(
                'SELECT price, volume FROM paper_trades WHERE symbol = %s AND direction = %s '
                'AND (voided = 0 OR voided IS NULL) '
                'ORDER BY COALESCE(trade_date, created_at::date::text), id',
                (order.symbol, Direction.SHORT.value)
            )
            sell_trades = cursor.fetchall()

            # 重构 FIFO lots（买）: [vol, price]
            lots = [[vol, price] for price, vol in buy_trades]
            fifo_cost = 0.0  # 历史累计成本（废弃，保留兼容性）

            # 历史卖出预消耗 lots
            # 【P0 Fix】SQL 返回 (price, volume) 顺序, 变量名要对应, 否则 rs 取到 price
            # 旧代码: for sell_vol, sell_price in sell_trades → sell_vol 实际是 price, 导致 rs=15.84 不是 800
            #         FIFO 几乎不消耗 lot, 后续 current_sell 凭空拿 12.45 成本算 profit → 幽灵超卖
            for sell_price_db, sell_vol_db in sell_trades:
                rs = sell_vol_db
                i = 0
                while rs > 0 and i < len(lots):
                    used = min(lots[i][0], rs)
                    fifo_cost += used * lots[i][1]
                    lots[i][0] -= used
                    rs -= used
                    if lots[i][0] == 0:
                        lots.pop(i)
                    else:
                        i += 1

            # 【P0 死防御】当前 sell 前先检查剩余可卖股数
            available_to_sell = sum(v for v, _ in lots)
            if order.volume > available_to_sell:
                print(
                    f"[ERR] 无法卖出 {order.symbol} x {order.volume}: "
                    f"FIFO 可用 {available_to_sell} (同日多次卖出或回放重放所致)"
                )
                cursor.execute(
                    'UPDATE paper_orders SET status = %s, updated_at = %s WHERE orderid = %s',
                    (OrderStatus.SUBMITTING.value, datetime.now().isoformat(), order_id)
                )
                conn.commit()
                conn.close()
                if order.symbol in self.positions:
                    self.positions[order.symbol].frozen -= order.volume
                self._save_account()
                return False

            # 当前卖出匹配 FIFO
            remaining_sell = order.volume
            current_sell_cost = 0.0
            i = 0
            while remaining_sell > 0 and i < len(lots):
                used = min(lots[i][0], remaining_sell)
                current_sell_cost += used * lots[i][1]
                lots[i][0] -= used
                remaining_sell -= used
                if lots[i][0] == 0:
                    lots.pop(i)
                else:
                    i += 1

            # 🔧 卖出费用: 佣金 0.05% + 印花税 0.1% = 0.15%
            SELL_FEE_RATE = 0.0015  # 佣金 0.05% + 印花税 0.1%
            sell_fee = fill_price * order.volume * SELL_FEE_RATE
            net_proceeds = fill_price * order.volume - sell_fee

            # FIFO 毛利 = 卖出金额 - FIFO 持仓成本
            fifo_gross_profit = fill_price * order.volume - current_sell_cost
            # 净利润 = 毛利 - 卖出手续费（佣金 + 印花税）
            net_profit = fifo_gross_profit - sell_fee

            # 更新剩余持仓的均价
            remaining_vol = sum(v for v, _ in lots)
            remaining_cost = sum(v * p for v, p in lots)

            self.available_cash += net_proceeds
            pos.volume = remaining_vol
            pos.avg_price = remaining_cost / remaining_vol if remaining_vol > 0 else 0.0
            pos.frozen -= order.volume

            # 记录成交
            td = self._trade_date or datetime.now().strftime('%Y-%m-%d')
            cursor.execute(
                'INSERT INTO paper_trades (orderid, symbol, direction, price, volume, amount, profit, created_at, trade_date, reason) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                (order_id, order.symbol, order.direction, fill_price, order.volume,
                 fill_price * order.volume, net_profit, datetime.now().isoformat(), td, getattr(order, 'reason', ''))
            )

            if pos.volume == 0:
                cursor.execute('DELETE FROM paper_positions WHERE symbol = %s', (order.symbol,))
                del self.positions[order.symbol]

        # 在同一事务内更新账户状态（行已由开头的 FOR UPDATE 锁定）
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute(
            'UPDATE paper_account_info SET initial_capital = %s, available_cash = %s, '
            'frozen_cash = %s, order_counter = %s, updated_at = %s WHERE id = 1',
            (self.initial_capital, self.available_cash, self.frozen_cash, self.order_counter, now)
        )

        conn.commit()
        conn.close()

        print(f"[OK] 成交：{order.symbol} @ {fill_price:.2f} x {order.volume}")
        return True
    
    def get_account_info(self, use_market_price: bool = False) -> dict:
        """
        获取账户信息
        
        Args:
            use_market_price: 是否使用实时市价计算（默认 False 使用成本价）
        """
        if use_market_price:
            # 使用实时市价计算
            try:
                # 使用绝对路径
                xueqiu_dir = str(get_xueqiu_dir())
                xueqiu_config = os.path.join(xueqiu_dir, 'config.json')
                
                if os.path.exists(xueqiu_config):
                    sys.path.insert(0, xueqiu_dir)
                    from xueqiu_engine import XueqiuEngine
                    xueqiu_engine = XueqiuEngine(config_file=xueqiu_config)
                    market_value = 0
                    for symbol, pos in self.positions.items():
                        try:
                            quote = xueqiu_engine.get_stock_quote(symbol)
                            current_price = quote.get('current', pos.avg_price)
                            market_value += current_price * pos.volume
                        except Exception as e:
                            print(f"[WARN]️ 获取 {symbol} 行情失败：{e}")
                            market_value += pos.avg_price * pos.volume
                else:
                    market_value = sum(pos.avg_price * pos.volume for pos in self.positions.values())
            except Exception as e:
                # 获取失败时使用成本价
                print(f"[WARN]️ 市价计算失败，使用成本价：{e}")
                market_value = sum(pos.avg_price * pos.volume for pos in self.positions.values())
        else:
            # 使用成本价计算
            market_value = sum(pos.avg_price * pos.volume for pos in self.positions.values())
        
        total_asset = self.available_cash + self.frozen_cash + market_value
        total_pnl = total_asset - self.initial_capital
        
        method_str = "(市价)" if use_market_price else "(成本)"
        
        return {
            '初始资金': f"{self.initial_capital:,.2f}",
            '可用资金': f"{self.available_cash:,.2f}",
            '冻结资金': f"{self.frozen_cash:,.2f}",
            '持仓市值': f"{market_value:,.2f} {method_str}",
            '总资产': f"{total_asset:,.2f}",
            '总盈亏': f"{total_pnl:,.2f} ({total_pnl/self.initial_capital*100:.2f}%)",
            '持仓数量': len(self.positions),
            '数据文件': self.db_file,
            '数据库': self.db_file
        }
    
    def get_positions(self) -> List[dict]:
        """获取当前持仓（包含股票名称）"""
        positions = []
        for symbol, pos in self.positions.items():
            positions.append({
                'symbol': symbol,
                'name': pos.name,  # 股票名称
                'volume': pos.volume,
                'avg_price': pos.avg_price,
                'cost_value': pos.avg_price * pos.volume
            })
        return positions
    
    def get_orders(self, symbol: str = None, status: str = None, limit: int = 100) -> List[dict]:
        """查询订单记录"""
        conn = self._get_pg_conn()
        cursor = conn.cursor()

        query = 'SELECT * FROM paper_orders WHERE 1=1'
        params = []

        if symbol:
            query += ' AND symbol = %s'
            params.append(symbol)

        if status:
            query += ' AND status = %s'
            params.append(status)

        query += ' ORDER BY created_at DESC LIMIT %s'
        params.append(limit)

        cursor.execute(query, params)
        cols = [desc[0] for desc in cursor.description]
        rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
        conn.close()

        return rows

    def get_trades(self, symbol: str = None, limit: int = 100) -> List[dict]:
        """查询成交记录"""
        conn = self._get_pg_conn()
        cursor = conn.cursor()

        query = 'SELECT * FROM paper_trades WHERE 1=1'
        params = []

        if symbol:
            query += ' AND symbol = %s'
            params.append(symbol)

        query += ' ORDER BY created_at DESC LIMIT %s'
        params.append(limit)

        cursor.execute(query, params)
        cols = [desc[0] for desc in cursor.description]
        rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
        conn.close()

        return rows

    def get_profit_summary(self) -> dict:
        """获取盈亏汇总"""
        conn = self._get_pg_conn()
        cursor = conn.cursor()

        cursor.execute('SELECT COALESCE(SUM(profit), 0) FROM paper_trades')
        total_profit = cursor.fetchone()[0] or 0

        cursor.execute(
            'SELECT symbol, COALESCE(SUM(profit), 0), COUNT(*) '
            'FROM paper_trades GROUP BY symbol ORDER BY 2 DESC'
        )
        by_symbol = cursor.fetchall()
        
        conn.close()
        
        return {
            '总盈亏': total_profit,
            '总交易次数': self.get_trade_count(),
            '按标的汇总': [(row[0], row[1], row[2]) for row in by_symbol]
        }
    
    def get_trade_count(self) -> int:
        """获取总交易次数"""
        conn = self._get_pg_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM paper_trades')
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def _save_order(self, order: Order):
        """保存订单到 PostgreSQL"""
        conn = self._get_pg_conn()
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO paper_orders (orderid, symbol, direction, price, volume, status, traded, created_at, updated_at, reason) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (order.orderid, order.symbol, order.direction, order.price, order.volume,
             order.status, order.traded, order.created_at, order.updated_at, getattr(order, 'reason', ''))
        )
        conn.commit()
        conn.close()
    
    def show_positions(self, use_market_price: bool = True):
        """
        显示持仓
        
        Args:
            use_market_price: 是否显示实时市价和盈亏
        """
        positions = self.get_positions()
        if not positions:
            print("[VIEW] 当前无持仓")
            return
        
        # 如果需要市价，获取实时价格
        current_prices = {}
        if use_market_price:
            try:
                xueqiu_dir = str(get_xueqiu_dir())
                xueqiu_config = os.path.join(xueqiu_dir, 'config.json')
                if os.path.exists(xueqiu_config):
                    sys.path.insert(0, xueqiu_dir)
                    from xueqiu_engine import XueqiuEngine
                    xueqiu_engine = XueqiuEngine(config_file=xueqiu_config)
                    for pos in positions:
                        try:
                            quote = xueqiu_engine.get_stock_quote(pos['symbol'])
                            current_prices[pos['symbol']] = quote.get('current', pos['avg_price'])
                        except:
                            current_prices[pos['symbol']] = pos['avg_price']
            except:
                pass
        
        print("\n[CHART] 当前持仓:")
        print("-" * 100)
        
        if use_market_price and current_prices:
            print(f"{'代码':<12} {'名称':<12} {'数量':>10} {'成本价':>12} {'当前价':>12} {'盈亏':>15}")
            print("-" * 120)
            
            total_pnl = 0
            for pos in positions:
                symbol = pos['symbol']
                name = pos.get('name', '')  # 股票名称
                current_price = current_prices.get(symbol, pos['avg_price'])
                pnl = (current_price - pos['avg_price']) * pos['volume']
                pnl_pct = (pnl / pos['cost_value'] * 100) if pos['cost_value'] > 0 else 0
                total_pnl += pnl
                
                status = '🟢' if pnl > 0 else '🔴' if pnl < 0 else '⚪'
                name_display = name if name else symbol
                print(f"{status} {symbol:<10} {name_display:<12} {pos['volume']:>10} {pos['avg_price']:>12.2f} {current_price:>12.2f} {pnl:>+14.2f} ({pnl_pct:+.2f}%)")
            
            print("-" * 120)
            print(f"{'持仓盈亏合计':>66} {total_pnl:>+15.2f}")
        else:
            print(f"{'代码':<12} {'数量':>10} {'成本价':>12} {'成本市值':>15}")
            print("-" * 80)
            for pos in positions:
                print(f"{pos['symbol']:<12} {pos['volume']:>10} {pos['avg_price']:>12.2f} {pos['cost_value']:>15.2f}")
        
        print("-" * 80)
    
    def show_orders(self, limit: int = 10):
        """显示订单记录"""
        orders = self.get_orders(limit=limit)
        if not orders:
            print("[VIEW] 暂无订单记录")
            return
        
        print(f"\n📋 最近 {len(orders)} 笔订单:")
        print("-" * 100)
        print(f"{'时间':<20} {'订单号':<15} {'代码':<12} {'方向':<8} {'价格':>10} {'数量':>10} {'状态':<10}")
        print("-" * 100)
        
        for o in orders:
            time_str = o['created_at'][:19].replace('T', ' ')
            print(f"{time_str:<20} {o['orderid']:<15} {o['symbol']:<12} {o['direction']:<8} {o['price']:>10.2f} {o['volume']:>10} {o['status']:<10}")
        
        print("-" * 100)
    
    def show_trades(self, limit: int = 10):
        """显示成交记录"""
        trades = self.get_trades(limit=limit)
        if not trades:
            print("[VIEW] 暂无成交记录")
            return
        
        print(f"\n[CHART] 最近 {len(trades)} 笔成交:")
        print("-" * 110)
        print(f"{'时间':<20} {'代码':<12} {'方向':<8} {'价格':>10} {'数量':>10} {'金额':>15} {'盈亏':>12}")
        print("-" * 110)
        
        for t in trades:
            time_str = t['created_at'][:19].replace('T', ' ')
            profit_str = f"{t['profit']:+.2f}" if t['profit'] != 0 else "-"
            print(f"{time_str:<20} {t['symbol']:<12} {t['direction']:<8} {t['price']:>10.2f} {t['volume']:>10} {t['amount']:>15.2f} {profit_str:>12}")
        
        print("-" * 110)
    
    def show_profit_summary(self):
        """显示盈亏汇总"""
        summary = self.get_profit_summary()
        
        print("\n" + "=" * 60)
        print("[CASH] 盈亏汇总")
        print("=" * 60)
        print(f"  总盈亏：{summary['总盈亏']:+.2f}")
        print(f"  总交易次数：{summary['总交易次数']}")
        
        if summary['按标的汇总']:
            print("\n  按标的汇总:")
            print(f"  {'代码':<12} {'盈亏':>15} {'交易次数':>10}")
            print("  " + "-" * 40)
            for symbol, profit, count in summary['按标的汇总']:
                print(f"  {symbol:<12} {profit:+>15.2f} {count:>10}")
        
        print("=" * 60)


def main():
    """演示持久化功能"""
    print("=" * 80)
    print("VN.PY 模拟交易引擎 - 持久化版本演示")
    print("=" * 80)
    
    # 创建引擎（使用当前目录的 data 文件夹）
    engine = PaperTradingEngine(data_dir="./data", initial_capital=1000000.0)
    
    print("\n📋 初始账户信息:")
    for k, v in engine.get_account_info().items():
        print(f"  {k}: {v}")
    
    # 执行交易
    print("\n" + "=" * 80)
    print("执行交易...")
    print("=" * 80)
    
    order1 = engine.buy("SH600519", 1700.00, 100)
    order2 = engine.buy("SZ000858", 45.50, 500)
    
    if order1:
        engine.match_order(order1, 1700.50)
    if order2:
        engine.match_order(order2, 45.48)
    
    # 查询
    print("\n" + "=" * 80)
    print("查询数据...")
    print("=" * 80)
    
    engine.show_positions()
    engine.show_orders()
    engine.show_trades()
    engine.show_profit_summary()
    
    print("\n" + "=" * 80)
    print("数据已持久化保存!")
    print(f"  数据库：{engine.db_file}")
    print("=" * 80)
    
    # 演示重新加载
    print("\n" + "=" * 80)
    print("重新加载账户（模拟程序重启）...")
    print("=" * 80)
    
    engine2 = PaperTradingEngine(data_dir="./data")
    engine2.show_positions()
    engine2.show_trades()
    
    print("\n[OK] 数据成功恢复！")


if __name__ == "__main__":
    main()
