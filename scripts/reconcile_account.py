#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
账户数据重算脚本 — 从 trades 表重放全部交易，修正 account_info 和卖出利润

修复内容：
1. account_info.available_cash: 基于交易记录 FIFO 重放重算（含买入手续费、卖出手续费+印花税）
2. account_info.frozen_cash: 基于未成交买入订单重新计算
3. trades.profit (卖出): 补充扣除卖出手续费（佣金 0.05% + 印花税 0.1%）
4. 排序策略: 与 paper_engine / portfolio API 对齐 (ORDER BY trade_date, id)

用法:
    # 预览模式（不修改数据）
    python reconcile_account.py --dry-run

    # 执行修复
    python reconcile_account.py

    # 指定数据库路径
    python reconcile_account.py --db path/to/trades.db

前置条件: 执行前请先停止 paper-trading 引擎
"""

import sqlite3
import os
import sys
import argparse
from datetime import datetime

# 修复 Windows GBK 编码问题
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# 费率常量（与 paper_engine.py 保持一致）
BUY_COMMISSION_RATE = 0.0005   # 买入佣金 0.05%
SELL_COMMISSION_RATE = 0.0005  # 卖出佣金 0.05%
STAMP_DUTY_RATE = 0.001        # 卖出印花税 0.1%
SELL_FEE_RATE = SELL_COMMISSION_RATE + STAMP_DUTY_RATE  # 卖出总费率 0.15%


def find_db_files():
    """查找所有 trades.db 文件"""
    db_files = []
    script_dir = os.path.dirname(os.path.abspath(__file__))
    search_dirs = [
        os.path.join(script_dir, '..', 'data'),
        os.path.join(script_dir, '..', 'backend', 'data'),
        os.path.join(script_dir, '..', 'apps', 'data'),
    ]
    for d in search_dirs:
        path = os.path.normpath(os.path.join(d, 'trades.db'))
        if os.path.exists(path):
            db_files.append(path)

    if not db_files:
        for root, dirs, files in os.walk(os.path.join(script_dir, '..')):
            if 'trades.db' in files:
                db_files.append(os.path.join(root, 'trades.db'))

    return list(set(db_files))


def reconcile(db_path: str, dry_run: bool = False):
    """重算单个数据库"""
    print(f'\n{"=" * 60}')
    print(f'数据库: {db_path}')
    print(f'模式: {"预览 (不修改)" if dry_run else "执行修复"}')
    print(f'{"=" * 60}')

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ── 1. 读取当前账户状态 ──
    cur.execute('SELECT * FROM account_info WHERE id = 1')
    acc_row = cur.fetchone()
    if not acc_row:
        print('[ERROR] account_info 表为空，请先初始化账户')
        conn.close()
        return

    acc = dict(acc_row)
    initial_capital = acc['initial_capital']
    old_available = acc['available_cash']
    old_frozen = acc['frozen_cash']

    print(f'\n修复前:')
    print(f'  initial_capital: {initial_capital:>14,.2f}')
    print(f'  available_cash:  {old_available:>14,.2f}')
    print(f'  frozen_cash:     {old_frozen:>14,.2f}')

    # ── 2. 读取全部成交记录（FIFO 排序：trade_date, id） ──
    cur.execute("""
        SELECT id, symbol, direction, price, volume, profit, amount
        FROM trades
        ORDER BY trade_date, id
    """)
    trades = cur.fetchall()

    # ── 3. FIFO 重放 ──
    available_cash = initial_capital
    positions = {}  # symbol -> [{'price': float, 'volume': int}, ...]
    corrections = []  # [(trade_id, old_profit, new_profit), ...]
    total_buy_cost = 0.0
    total_sell_proceeds = 0.0
    total_sell_fee = 0.0
    buy_count = 0
    sell_count = 0

    for trade in trades:
        tid = trade['id']
        symbol = trade['symbol']
        direction = trade['direction']
        price = trade['price']
        volume = trade['volume']
        old_profit = trade['profit'] or 0

        if direction == '买入':
            # 买入：扣除含手续费金额
            buy_cost = price * volume * (1 + BUY_COMMISSION_RATE)
            available_cash -= buy_cost
            total_buy_cost += buy_cost
            buy_count += 1

            # 记录到 FIFO 持仓
            if symbol not in positions:
                positions[symbol] = []
            positions[symbol].append({'price': price, 'volume': volume})

        elif direction == '卖出':
            lots = positions.get(symbol, [])
            if not lots:
                print(f'  [WARN] 卖出 {symbol} x{volume} 但 FIFO 无可卖持仓，跳过资金计算')
                continue

            # FIFO 计算卖出成本
            remaining = volume
            sell_cost_basis = 0.0
            i = 0
            while remaining > 0 and i < len(lots):
                used = min(lots[i]['volume'], remaining)
                sell_cost_basis += used * lots[i]['price']
                lots[i]['volume'] -= used
                remaining -= used
                if lots[i]['volume'] == 0:
                    lots.pop(i)
                else:
                    i += 1

            # 卖出金额与手续费
            gross_amount = price * volume
            sell_fee = gross_amount * SELL_FEE_RATE
            net_proceeds = gross_amount - sell_fee
            net_profit = gross_amount - sell_cost_basis - sell_fee

            available_cash += net_proceeds
            total_sell_proceeds += net_proceeds
            total_sell_fee += sell_fee
            sell_count += 1

            # 检查 profit 是否需要修正
            if abs(net_profit - old_profit) > 0.01:
                corrections.append((tid, old_profit, net_profit))
                if not dry_run:
                    cur.execute(
                        'UPDATE trades SET profit = ?, amount = ? WHERE id = ?',
                        (round(net_profit, 4), round(gross_amount, 4), tid)
                    )

    # ── 4. 计算待冻结资金（未成交买入订单） ──
    cur.execute("""
        SELECT orderid, symbol, direction, price, volume, status
        FROM orders
        WHERE status NOT IN ('全部成交', '已撤销', '拒单')
    """)
    pending_orders = cur.fetchall()

    frozen_cash = 0.0
    for o in pending_orders:
        if o['direction'] == '买入':
            frozen = o['price'] * o['volume']
            if o['symbol'].startswith('SH') or o['symbol'].startswith('SZ'):
                frozen *= (1 + BUY_COMMISSION_RATE)
            frozen_cash += frozen

    # available_cash 中扣除冻结部分
    calculated_available = available_cash - frozen_cash

    # ── 5. 剩余持仓摘要 ──
    remaining_positions = []
    for symbol, lots in positions.items():
        total_vol = sum(l['volume'] for l in lots)
        if total_vol > 0:
            weighted_price = sum(l['price'] * l['volume'] for l in lots) / total_vol
            remaining_positions.append({
                'symbol': symbol,
                'volume': total_vol,
                'avg_cost': weighted_price,
                'total_cost': weighted_price * total_vol,
            })

    # ── 6. 应用修复 ──
    if not dry_run:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cur.execute("""
            INSERT OR REPLACE INTO account_info (id, initial_capital, available_cash, frozen_cash, order_counter, updated_at)
            VALUES (1, ?, ?, ?, COALESCE((SELECT order_counter FROM account_info WHERE id=1), 0), ?)
        """, (initial_capital, calculated_available, frozen_cash, now))
        conn.commit()
        print(f'\n  [OK] 账户数据已写入数据库')

    # ── 7. 输出报告 ──
    print(f'\n交易统计:')
    print(f'  买入 {buy_count} 笔, 总支出(含佣金): {total_buy_cost:>14,.2f}')
    print(f'  卖出 {sell_count} 笔, 净收入(扣费后): {total_sell_proceeds:>14,.2f}')
    print(f'  卖出总费用(佣金+印花税):          {total_sell_fee:>14,.2f}')

    if corrections:
        print(f'\n利润修正 ({len(corrections)} 笔卖出):')
        for tid, old_p, new_p in corrections:
            print(f'  trade#{tid}: CNY {old_p:+,.2f} -> CNY {new_p:+,.2f} (diff: {new_p - old_p:+,.2f})')

    print(f'\n修正后:')
    print(f'  available_cash:  {calculated_available:>14,.2f} (变化: {calculated_available - old_available:+,.2f})')
    print(f'  frozen_cash:     {frozen_cash:>14,.2f} (变化: {frozen_cash - old_frozen:+,.2f})')

    if remaining_positions:
        print(f'\n当前持仓 (FIFO 重算):')
        for pos in remaining_positions:
            print(f'  {pos["symbol"]}: {pos["volume"]}股, 成本均价 ¥{pos["avg_cost"]:.2f}, 总成本 ¥{pos["total_cost"]:,.2f}')
    else:
        print(f'\n当前持仓: 无')

    conn.close()

    if dry_run:
        print(f'\n[INFO] 预览完成。使用不带 --dry-run 参数执行实际修复。')
    else:
        print(f'\n[OK] 修复完成。请重启 paper-trading 引擎以使内存状态同步。')

    # 返回修正数（供调用方使用）
    return len(corrections)


def main():
    parser = argparse.ArgumentParser(description='账户数据重算 — 从交易记录重放修复账户状态')
    parser.add_argument('--dry-run', action='store_true', help='预览模式，不修改数据')
    parser.add_argument('--db', type=str, default=None, help='指定数据库路径（默认自动查找）')
    args = parser.parse_args()

    if args.db:
        db_files = [args.db]
    else:
        db_files = find_db_files()

    if not db_files:
        print('[ERROR] 未找到 trades.db 文件。请用 --db 指定路径。')
        sys.exit(1)

    print(f'找到 {len(db_files)} 个 trades.db:')
    for f in db_files:
        print(f'  {f}')

    total_corrections = 0
    for db_path in db_files:
        try:
            result = reconcile(db_path, dry_run=args.dry_run)
            if result:
                total_corrections += result
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f'[ERROR] 处理 {db_path} 失败: {e}')

    print(f'\n{"=" * 60}')
    print(f'全部完成! 共修正 {total_corrections} 笔卖出利润记录。')


if __name__ == '__main__':
    main()
