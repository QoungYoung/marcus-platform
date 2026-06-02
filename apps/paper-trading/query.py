#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VN.PY 模拟交易查询工具

支持查询：
- 账户信息
- 当前持仓
- 订单记录
- 成交记录
- 盈亏汇总
"""

import sys
import os
import argparse

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paper_engine import PaperTradingEngine


def cmd_account(engine, args=None):
    """显示账户信息"""
    # 默认使用市价计算盈亏
    use_market_price = True
    
    print("\n" + "=" * 60)
    print("📊 账户信息 (实时市价)")
    print("=" * 60)
    info = engine.get_account_info(use_market_price=use_market_price)
    for k, v in info.items():
        print(f"  {k}: {v}")
    print("=" * 60)


def cmd_positions(engine, args=None):
    """显示持仓"""
    # 默认使用市价显示盈亏
    engine.show_positions(use_market_price=True)


def cmd_orders(engine, args=None):
    """显示订单"""
    limit = getattr(args, 'limit', 20) if args else 20
    symbol = getattr(args, 'symbol', None) if args else None
    engine.show_orders(limit=limit)


def cmd_trades(engine, args=None):
    """显示成交"""
    limit = getattr(args, 'limit', 20) if args else 20
    symbol = getattr(args, 'symbol', None) if args else None
    engine.show_trades(limit=limit)


def cmd_profit(engine, args=None):
    """显示盈亏"""
    engine.show_profit_summary()


def cmd_buy(engine, args):
    """买入"""
    order_id = engine.buy(args.symbol, args.price, args.volume)
    if order_id:
        print(f"✓ 委托成功，订单号：{order_id}")
        # 自动成交（演示用）
        engine.match_order(order_id, args.price)


def cmd_sell(engine, args):
    """卖出"""
    order_id = engine.sell(args.symbol, args.price, args.volume)
    if order_id:
        print(f"✓ 委托成功，订单号：{order_id}")
        # 自动成交（演示用）
        engine.match_order(order_id, args.price)


def main():
    parser = argparse.ArgumentParser(description='VN.PY 模拟交易查询工具')
    parser.add_argument('--data-dir', default='./data', help='数据目录')
    parser.add_argument('--init-capital', type=float, default=1000000.0, help='初始资金')
    
    subparsers = parser.add_subparsers(dest='command', help='命令')
    
    # account 命令
    account_parser = subparsers.add_parser('account', help='显示账户信息')
    account_parser.add_argument('-m', '--market-price', action='store_true', help='使用实时市价计算盈亏')
    
    # positions 命令
    subparsers.add_parser('positions', help='显示持仓')
    
    # orders 命令
    orders_parser = subparsers.add_parser('orders', help='显示订单')
    orders_parser.add_argument('-l', '--limit', type=int, default=20, help='显示数量')
    orders_parser.add_argument('-s', '--symbol', help='标的代码')
    
    # trades 命令
    trades_parser = subparsers.add_parser('trades', help='显示成交')
    trades_parser.add_argument('-l', '--limit', type=int, default=20, help='显示数量')
    trades_parser.add_argument('-s', '--symbol', help='标的代码')
    
    # profit 命令
    subparsers.add_parser('profit', help='显示盈亏')
    
    # buy 命令
    buy_parser = subparsers.add_parser('buy', help='买入')
    buy_parser.add_argument('symbol', help='标的代码')
    buy_parser.add_argument('price', type=float, help='价格')
    buy_parser.add_argument('volume', type=int, help='数量')
    
    # sell 命令
    sell_parser = subparsers.add_parser('sell', help='卖出')
    sell_parser.add_argument('symbol', help='标的代码')
    sell_parser.add_argument('price', type=float, help='价格')
    sell_parser.add_argument('volume', type=int, help='数量')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(0)
    
    # 创建引擎
    engine = PaperTradingEngine(data_dir=args.data_dir, initial_capital=args.init_capital)
    
    # 执行命令
    commands = {
        'account': cmd_account,
        'positions': cmd_positions,
        'orders': cmd_orders,
        'trades': cmd_trades,
        'profit': cmd_profit,
        'buy': cmd_buy,
        'sell': cmd_sell,
    }
    
    if args.command in commands:
        commands[args.command](engine, args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
