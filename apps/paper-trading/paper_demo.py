#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VN.PY 模拟交易演示脚本
简易版模拟交易引擎（无需 GUI）
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional
from enum import Enum


class Direction(Enum):
    LONG = "多头"
    SHORT = "空头"


class Status(Enum):
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
    direction: Direction
    price: float
    volume: int
    status: Status = Status.SUBMITTING
    traded: int = 0
    created_at: datetime = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()


@dataclass
class Position:
    symbol: str
    volume: int = 0
    avg_price: float = 0.0
    frozen: int = 0


class PaperTradingEngine:
    """
    简易模拟交易引擎
    
    功能：
    - 模拟资金账户
    - 模拟持仓管理
    - 模拟订单撮合
    - 实时盈亏计算
    """
    
    def __init__(self, initial_capital: float = 1000000.0):
        self.initial_capital = initial_capital
        self.available_cash = initial_capital
        self.frozen_cash = 0.0
        self.orders: Dict[str, Order] = {}
        self.positions: Dict[str, Position] = {}
        self.trades: List[dict] = []
        self.order_counter = 0
        
    def buy(self, symbol: str, price: float, volume: int) -> Optional[str]:
        """买入股票/期货"""
        if symbol.startswith("SH") or symbol.startswith("SZ"):
            required_cash = price * volume * 1.0005
        else:
            required_cash = price * volume * 100 * 0.1
        
        if required_cash > self.available_cash:
            print(f"❌ 资金不足！需要 {required_cash:.2f}, 可用 {self.available_cash:.2f}")
            return None
        
        self.available_cash -= required_cash
        self.frozen_cash += required_cash
        
        self.order_counter += 1
        order_id = f"ORD{self.order_counter:06d}"
        order = Order(
            orderid=order_id,
            symbol=symbol,
            direction=Direction.LONG,
            price=price,
            volume=volume,
            status=Status.SUBMITTING
        )
        self.orders[order_id] = order
        
        print(f"📈 买入委托：{symbol} @ {price:.2f} x {volume} | 订单号：{order_id}")
        return order_id
    
    def sell(self, symbol: str, price: float, volume: int) -> Optional[str]:
        """卖出股票/期货"""
        if symbol not in self.positions:
            print(f"❌ 没有 {symbol} 的持仓")
            return None
        
        pos = self.positions[symbol]
        if pos.volume - pos.frozen < volume:
            print(f"❌ 持仓不足！可用 {pos.volume - pos.frozen}, 欲卖 {volume}")
            return None
        
        pos.frozen += volume
        
        self.order_counter += 1
        order_id = f"ORD{self.order_counter:06d}"
        order = Order(
            orderid=order_id,
            symbol=symbol,
            direction=Direction.SHORT,
            price=price,
            volume=volume,
            status=Status.SUBMITTING
        )
        self.orders[order_id] = order
        
        print(f"📉 卖出委托：{symbol} @ {price:.2f} x {volume} | 订单号：{order_id}")
        return order_id
    
    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        if order_id not in self.orders:
            return False
        
        order = self.orders[order_id]
        if order.status in [Status.ALLTRADED, Status.CANCELLED, Status.REJECTED]:
            return False
        
        if order.direction == Direction.LONG:
            required_cash = order.price * order.volume
            self.frozen_cash -= required_cash
            self.available_cash += required_cash
        
        order.status = Status.CANCELLED
        print(f"✅ 已撤销订单：{order_id}")
        return True
    
    def match_order(self, order_id: str, fill_price: float):
        """模拟订单成交"""
        if order_id not in self.orders:
            return
        
        order = self.orders[order_id]
        order.status = Status.ALLTRADED
        order.traded = order.volume
        
        if order.symbol not in self.positions:
            self.positions[order.symbol] = Position(symbol=order.symbol)
        
        pos = self.positions[order.symbol]
        
        if order.direction == Direction.LONG:
            total_cost = pos.avg_price * pos.volume + fill_price * order.volume
            pos.volume += order.volume
            pos.avg_price = total_cost / pos.volume if pos.volume > 0 else 0
            self.frozen_cash -= order.price * order.volume
            
            self.trades.append({
                'time': datetime.now(),
                'action': 'BUY',
                'symbol': order.symbol,
                'price': fill_price,
                'volume': order.volume,
                'amount': fill_price * order.volume
            })
        else:
            profit = (fill_price - pos.avg_price) * order.volume
            self.available_cash += fill_price * order.volume
            pos.volume -= order.volume
            pos.frozen -= order.volume
            
            self.trades.append({
                'time': datetime.now(),
                'action': 'SELL',
                'symbol': order.symbol,
                'price': fill_price,
                'volume': order.volume,
                'amount': fill_price * order.volume,
                'profit': profit
            })
            
            if pos.volume == 0:
                del self.positions[order.symbol]
        
        print(f"✅ 成交：{order.symbol} @ {fill_price:.2f} x {order.volume}")
    
    def get_account_info(self) -> dict:
        """获取账户信息"""
        market_value = sum(pos.avg_price * pos.volume for pos in self.positions.values())
        total_asset = self.available_cash + self.frozen_cash + market_value
        total_pnl = total_asset - self.initial_capital
        
        return {
            '初始资金': f"{self.initial_capital:,.2f}",
            '可用资金': f"{self.available_cash:,.2f}",
            '冻结资金': f"{self.frozen_cash:,.2f}",
            '持仓市值': f"{market_value:,.2f}",
            '总资产': f"{total_asset:,.2f}",
            '总盈亏': f"{total_pnl:,.2f} ({total_pnl/self.initial_capital*100:.2f}%)",
            '持仓数量': len(self.positions),
            '订单数量': len(self.orders),
            '成交数量': len(self.trades)
        }
    
    def show_positions(self):
        """显示持仓"""
        if not self.positions:
            print("📭 当前无持仓")
            return
        
        print("\n📊 当前持仓:")
        print("-" * 80)
        print(f"{'代码':<12} {'数量':>10} {'成本价':>12} {'成本市值':>15}")
        print("-" * 80)
        
        for symbol, pos in self.positions.items():
            cost_value = pos.avg_price * pos.volume
            print(f"{symbol:<12} {pos.volume:>10} {pos.avg_price:>12.2f} {cost_value:>15.2f}")
        
        print("-" * 80)
    
    def show_trades(self, limit: int = 10):
        """显示成交记录"""
        if not self.trades:
            print("📭 暂无成交记录")
            return
        
        print(f"\n💹 最近 {min(limit, len(self.trades))} 笔成交:")
        print("-" * 90)
        print(f"{'时间':<20} {'操作':<8} {'代码':<12} {'价格':>10} {'数量':>10} {'金额':>15}")
        print("-" * 90)
        
        for trade in self.trades[-limit:]:
            time_str = trade['time'].strftime('%Y-%m-%d %H:%M')
            action = '📈买入' if trade['action'] == 'BUY' else '📉卖出'
            profit_str = f" (盈亏：{trade.get('profit', 0):+.2f})" if 'profit' in trade else ""
            print(f"{time_str:<20} {action:<8} {trade['symbol']:<12} {trade['price']:>10.2f} {trade['volume']:>10} {trade['amount']:>15.2f}{profit_str}")
        
        print("-" * 90)


def main():
    """演示模拟交易"""
    print("=" * 80)
    print("VN.PY 模拟交易演示")
    print("=" * 80)
    
    engine = PaperTradingEngine(initial_capital=1000000.0)
    
    print("\n📋 初始账户信息:")
    for k, v in engine.get_account_info().items():
        print(f"  {k}: {v}")
    
    print("\n" + "=" * 80)
    print("执行买入操作...")
    print("=" * 80)
    
    order1 = engine.buy("SH600519", 1700.00, 100)
    order2 = engine.buy("SZ000858", 45.50, 500)
    order3 = engine.buy("SH601318", 55.00, 1000)
    
    print("\n" + "=" * 80)
    print("模拟成交...")
    print("=" * 80)
    
    if order1:
        engine.match_order(order1, 1700.50)
    if order2:
        engine.match_order(order2, 45.48)
    if order3:
        engine.match_order(order3, 55.10)
    
    print("\n" + "=" * 80)
    print("当前账户信息:")
    print("=" * 80)
    for k, v in engine.get_account_info().items():
        print(f"  {k}: {v}")
    
    engine.show_positions()
    engine.show_trades()
    
    print("\n" + "=" * 80)
    print("执行卖出操作...")
    print("=" * 80)
    
    order4 = engine.sell("SH600519", 1720.00, 50)
    if order4:
        engine.match_order(order4, 1718.00)
    
    print("\n" + "=" * 80)
    print("最终账户信息:")
    print("=" * 80)
    for k, v in engine.get_account_info().items():
        print(f"  {k}: {v}")
    
    engine.show_positions()
    engine.show_trades()
    
    print("\n" + "=" * 80)
    print("演示结束！")
    print("=" * 80)


if __name__ == "__main__":
    main()
