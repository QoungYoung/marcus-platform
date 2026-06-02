#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新持仓市价 - 修复账户盈亏显示

从雪球获取实时价格，更新账户市值计算
"""

import sys
import os
import json

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paper_engine import PaperTradingEngine

# 导入雪球引擎获取实时价格
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'xueqiu-data-query'))
from xueqiu_engine import XueqiuEngine


def update_market_prices(data_dir: str = "./data"):
    """
    更新持仓市价并显示真实盈亏
    """
    # 加载账户
    account_file = os.path.join(data_dir, "account.json")
    if not os.path.exists(account_file):
        print("❌ 账户文件不存在")
        return
    
    with open(account_file, 'r', encoding='utf-8') as f:
        account_data = json.load(f)
    
    initial_capital = account_data.get('initial_capital', 1000000.0)
    available_cash = account_data.get('available_cash', 0)
    positions = account_data.get('positions', {})
    
    # 初始化雪球引擎
    xueqiu_dir = os.path.join(os.path.dirname(__file__), '..', 'xueqiu-data-query')
    xueqiu_config = os.path.join(xueqiu_dir, 'config.json')
    
    if os.path.exists(xueqiu_config):
        xueqiu_engine = XueqiuEngine(config_file=xueqiu_config)
    else:
        print("⚠️ 雪球配置不存在，使用成本价计算")
        xueqiu_engine = None
    
    print("=" * 80)
    print("📊 持仓盈亏分析 (实时市价)")
    print("=" * 80)
    print()
    
    total_cost = 0
    total_market = 0
    
    for symbol, pos in positions.items():
        volume = pos.get('volume', 0)
        avg_price = pos.get('avg_price', 0)
        cost_value = volume * avg_price
        
        # 获取实时价格
        if xueqiu_engine:
            try:
                quote = xueqiu_engine.get_stock_quote(symbol)
                current_price = quote.get('current', avg_price)
            except:
                current_price = avg_price
        else:
            current_price = avg_price
        
        market_value = volume * current_price
        pnl = market_value - cost_value
        pnl_pct = (pnl / cost_value * 100) if cost_value > 0 else 0
        
        total_cost += cost_value
        total_market += market_value
        
        status = '🟢' if pnl > 0 else '🔴' if pnl < 0 else '⚪'
        
        print(f"{status} {symbol}:")
        print(f"   持仓：{volume} 股")
        print(f"   成本：{avg_price:.2f} × {volume} = {cost_value:.0f}")
        print(f"   当前：{current_price:.2f} × {volume} = {market_value:.0f}")
        print(f"   盈亏：{pnl:+.0f} ({pnl_pct:+.2f}%)")
        print()
    
    print("=" * 80)
    print(f"总成本市值：{total_cost:,.0f}")
    print(f"当前市值：{total_market:,.0f}")
    print(f"持仓盈亏：{total_market - total_cost:+,.0f} ({(total_market - total_cost)/total_cost*100:+.2f}%)")
    print()
    
    # 计算总资产（按市价）
    total_asset = available_cash + total_market
    total_pnl = total_asset - initial_capital
    
    print("=" * 80)
    print("📋 账户总览 (市价法)")
    print("=" * 80)
    print(f"  初始资金：{initial_capital:,.2f}")
    print(f"  可用资金：{available_cash:,.2f}")
    print(f"  持仓市值：{total_market:,.2f} (市价)")
    print(f"  总资产：{total_asset:,.2f}")
    print(f"  总盈亏：{total_pnl:+,.2f} ({total_pnl/initial_capital*100:+.2f}%)")
    print("=" * 80)
    
    # 保存市价快照
    snapshot_file = os.path.join(data_dir, "market_snapshot.json")
    snapshot = {
        "timestamp": __import__('datetime').datetime.now().isoformat(),
        "available_cash": available_cash,
        "positions": {},
        "total_market": total_market,
        "total_asset": total_asset,
        "total_pnl": total_pnl
    }
    
    for symbol, pos in positions.items():
        volume = pos.get('volume', 0)
        avg_price = pos.get('avg_price', 0)
        if xueqiu_engine:
            try:
                quote = xueqiu_engine.get_stock_quote(symbol)
                current_price = quote.get('current', avg_price)
            except:
                current_price = avg_price
        else:
            current_price = avg_price
        
        snapshot["positions"][symbol] = {
            "volume": volume,
            "avg_price": avg_price,
            "current_price": current_price,
            "cost_value": volume * avg_price,
            "market_value": volume * current_price,
            "pnl": volume * current_price - volume * avg_price
        }
    
    with open(snapshot_file, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    
    print(f"\n✓ 市价快照已保存：{snapshot_file}")


if __name__ == "__main__":
    update_market_prices()
