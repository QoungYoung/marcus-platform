#!/usr/bin/env python3
"""Update paper_engine.py to include reason in trades table."""
import re

path = 'apps/paper-trading/paper_engine.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add migration for reason column (after the voided_at migration)
migration_block = """        # 迁移: 交易撤回功能（voided=1 的成交不计入持仓）
        try:
            cursor.execute('ALTER TABLE trades ADD COLUMN voided INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE trades ADD COLUMN void_reason TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE trades ADD COLUMN voided_at TEXT')
        except sqlite3.OperationalError:
            pass"""

new_migration_block = """        # 迁移: 交易撤回功能（voided=1 的成交不计入持仓）
        try:
            cursor.execute('ALTER TABLE trades ADD COLUMN voided INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE trades ADD COLUMN void_reason TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE trades ADD COLUMN voided_at TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE trades ADD COLUMN reason TEXT')
        except sqlite3.OperationalError:
            pass"""

assert migration_block in content, "Migration block not found"
content = content.replace(migration_block, new_migration_block)
print("1. Migration for reason column added")

# 2. Update first INSERT INTO trades (buy) - add reason column + value
old_buy = "INSERT INTO trades (orderid, symbol, direction, price, volume, amount, profit, created_at, trade_date)"
new_buy = "INSERT INTO trades (orderid, symbol, direction, price, volume, amount, profit, created_at, trade_date, reason)"
content = content.replace(old_buy, new_buy)

old_buy_vals = "(order_id, order.symbol, order.direction, fill_price, order.volume,\n                  fill_price * order.volume, 0, datetime.now().isoformat(), td))"
new_buy_vals = "(order_id, order.symbol, order.direction, fill_price, order.volume,\n                  fill_price * order.volume, 0, datetime.now().isoformat(), td, getattr(order, 'reason', '')))"
content = content.replace(old_buy_vals, new_buy_vals)
print("2. Buy INSERT updated")

# 3. Update second INSERT INTO trades (sell) - add reason column + value
# Find the second occurrence
first_idx = content.find("INSERT INTO trades (orderid, symbol, direction, price, volume, amount, profit, created_at, trade_date)")
second_idx = content.find("INSERT INTO trades (orderid, symbol, direction, price, volume, amount, profit, created_at, trade_date)", first_idx + 1)
if second_idx > 0:
    content = content[:second_idx] + content[second_idx:].replace(
        "INSERT INTO trades (orderid, symbol, direction, price, volume, amount, profit, created_at, trade_date)",
        "INSERT INTO trades (orderid, symbol, direction, price, volume, amount, profit, created_at, trade_date, reason)",
        1
    )
    print("3. Sell INSERT column updated")

# Find sell values pattern - it's different from buy
old_sell_vals = "(order_id, order.symbol, order.direction, fill_price, order.volume,\n                      fill_price * order.volume, profit, datetime.now().isoformat(), td))"
new_sell_vals = "(order_id, order.symbol, order.direction, fill_price, order.volume,\n                      fill_price * order.volume, profit, datetime.now().isoformat(), td, getattr(order, 'reason', '')))"
if old_sell_vals in content:
    content = content.replace(old_sell_vals, new_sell_vals)
    print("4. Sell INSERT values updated")
else:
    # Try alternate pattern
    print("4. Alternate sell pattern check...")
    idx = content.find("direction, fill_price, order.volume,")
    if idx > 0:
        snippet = content[idx-100:idx+200]
        print(repr(snippet[:300]))

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Done!")
