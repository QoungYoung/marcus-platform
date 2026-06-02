#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 trades.db 中的账户数据
- 清理 frozen_cash 残留（手续费解冻不完全的 bug）
- 基于交易记录重新计算 available_cash
"""
import sqlite3
import os
import glob

def find_db_files():
    """查找所有 trades.db 文件"""
    db_files = []
    search_dirs = [
        os.path.join(os.path.dirname(__file__), '..', 'data'),
        os.path.join(os.path.dirname(__file__), '..', 'backend', 'data'),
        os.path.join(os.path.dirname(__file__), '..', 'apps', 'data'),
    ]
    for d in search_dirs:
        path = os.path.normpath(os.path.join(d, 'trades.db'))
        if os.path.exists(path):
            db_files.append(path)
    
    # Fallback: recursive search
    if not db_files:
        for root, dirs, files in os.walk(os.path.join(os.path.dirname(__file__), '..')):
            if 'trades.db' in files:
                db_files.append(os.path.join(root, 'trades.db'))
    
    return list(set(db_files))

def fix_account(db_path):
    """修复单个数据库的账户数据"""
    print(f'\n{"="*60}')
    print(f'处理: {db_path}')
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # 1. 读取当前账户
    cur.execute('SELECT * FROM account_info WHERE id=1')
    acc = dict(cur.fetchone())
    initial_capital = acc['initial_capital']
    old_available = acc['available_cash']
    old_frozen = acc['frozen_cash']
    
    print(f'修复前:')
    print(f'  initial_capital: {initial_capital:,.2f}')
    print(f'  available_cash:  {old_available:,.2f}')
    print(f'  frozen_cash:     {old_frozen:,.2f}')
    
    # 2. 检查未完成订单（用于计算 frozen_cash）
    cur.execute("""
        SELECT orderid, symbol, direction, price, volume, status 
        FROM orders 
        WHERE status NOT IN ('全部成交','已撤销','拒单')
    """)
    pending_orders = cur.fetchall()
    
    # 3. 根据交易记录计算真实资金
    # available_cash = initial - 买入总额 + 卖出总额
    cur.execute("""
        SELECT direction, SUM(price * volume) as total 
        FROM trades 
        GROUP BY direction
    """)
    trade_sums = {r['direction']: r['total'] for r in cur.fetchall()}
    
    total_buy = trade_sums.get('买入', 0)
    total_sell = trade_sums.get('卖出', 0)
    
    # 计算正确的 frozen_cash（仅未完成订单）
    calculated_frozen = 0
    if pending_orders:
        print(f'\n  未完成订单: {len(pending_orders)} 笔')
        for o in pending_orders:
            frozen = o['price'] * o['volume']
            if o['symbol'].startswith('SH') or o['symbol'].startswith('SZ'):
                frozen *= 1.0005
            calculated_frozen += frozen
            print(f'    {o["orderid"]}: {o["direction"]} {o["symbol"]} @{o["price"]}x{o["volume"]} 冻结={frozen:,.2f}')
    
    calculated_available = initial_capital - total_buy + total_sell - calculated_frozen
    
    print(f'\n  交易统计:')
    print(f'    买入总额: {total_buy:,.2f}')
    print(f'    卖出总额: {total_sell:,.2f}')
    print(f'    已实现盈亏: {total_sell - total_buy:+,.2f}')
    
    # 4. 更新账户
    cur.execute("""
        UPDATE account_info 
        SET available_cash = ?, frozen_cash = ?, updated_at = datetime('now','localtime')
        WHERE id = 1
    """, (calculated_available, calculated_frozen))
    conn.commit()
    
    print(f'\n修复后:')
    print(f'  available_cash: {calculated_available:,.2f} (变化: {calculated_available - old_available:+,.2f})')
    print(f'  frozen_cash:    {calculated_frozen:,.2f} (变化: {calculated_frozen - old_frozen:+,.2f})')
    
    conn.close()
    print(f'[OK] 修复完成')

def main():
    db_files = find_db_files()
    if not db_files:
        print('未找到 trades.db 文件')
        return
    
    print(f'找到 {len(db_files)} 个 trades.db 文件:')
    for f in db_files:
        print(f'  {f}')
    
    for db_path in db_files:
        try:
            fix_account(db_path)
        except Exception as e:
            print(f'[ERROR] 修复失败: {e}')
    
    print(f'\n{"="*60}')
    print('[OK] 全部完成! 请重启服务使代码修复生效。')

if __name__ == '__main__':
    main()
