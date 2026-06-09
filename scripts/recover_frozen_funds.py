# -*- coding: utf-8 -*-
"""恢复被冻结的资金 — 取消所有未成交订单，解冻资金"""
import sqlite3, sys, os

# 支持直接运行或从项目根运行
db_candidates = [
    "data/trades.db",
    os.path.join(os.path.dirname(__file__), "..", "data", "trades.db"),
]
db_path = None
for p in db_candidates:
    if os.path.exists(p):
        db_path = p
        break

if not db_path:
    print("ERROR: trades.db not found!")
    sys.exit(1)

conn = sqlite3.connect(db_path)
c = conn.cursor()

# 1. 查看当前状态
c.execute("SELECT id, initial_capital, available_cash, frozen_cash FROM account_info WHERE id=1")
row = c.fetchone()
if not row:
    print("ERROR: account_info not found!")
    conn.close()
    sys.exit(1)

print("=" * 50)
print("Account Status BEFORE Fix")
print("=" * 50)
print(f"  Initial Capital:  {row[1]:>12,.2f}")
print(f"  Available Cash:   {row[2]:>12,.2f}")
print(f"  Frozen Cash:      {row[3]:>12,.2f}")

# 2. 查找所有 SUBMITTING / SUBMITTED 订单
c.execute("""
    SELECT orderid, symbol, direction, price, volume, status 
    FROM orders 
    WHERE status IN ('submitting', 'submitted')
""")
stuck_orders = c.fetchall()

print(f"\n  Stuck Orders:     {len(stuck_orders)}")
total_frozen = 0
for o in stuck_orders:
    frozen = o[3] * o[4] * 1.0005 if (o[1].startswith('SH') or o[1].startswith('SZ')) else o[3] * o[4] * 100 * 0.1
    total_frozen += frozen
    print(f"    {o[0]} {o[1]} {o[2]} {o[3]:.2f}*{o[4]} {o[5]}  ~{frozen:,.2f}")

if not stuck_orders:
    print("\nNo stuck orders found. Nothing to fix.")
    conn.close()
    sys.exit(0)

# 3. Confirm
print(f"\nTotal to unfreeze: ~{total_frozen:,.2f}")
confirm = input("\nCancel all stuck orders and unfreeze funds? (yes/no): ")
if confirm.lower() != 'yes':
    print("Cancelled.")
    conn.close()
    sys.exit(0)

# 4. Cancel orders + unfreeze
now = __import__('datetime').datetime.now().isoformat()
for o in stuck_orders:
    order_id = o[0]
    symbol = o[1]
    direction = o[2]
    price = o[3]
    volume = o[4]

    # Mark order as cancelled
    c.execute("UPDATE orders SET status='cancelled', updated_at=? WHERE orderid=?", (now, order_id))

    # Calculate frozen amount
    if symbol.startswith('SH') or symbol.startswith('SZ'):
        frozen_amount = price * volume * 1.0005
    else:
        frozen_amount = price * volume * 100 * 0.1

    # Unfreeze in account_info
    c.execute("UPDATE account_info SET frozen_cash = frozen_cash - ?, available_cash = available_cash + ? WHERE id=1",
              (frozen_amount, frozen_amount))

conn.commit()

# 5. Verify
c.execute("SELECT available_cash, frozen_cash FROM account_info WHERE id=1")
row = c.fetchone()
print("\n" + "=" * 50)
print("Account Status AFTER Fix")
print("=" * 50)
print(f"  Available Cash:   {row[0]:>12,.2f}")
print(f"  Frozen Cash:      {row[1]:>12,.2f}")
print(f"\n  Unfrozen:         {total_frozen:>12,.2f}")
print(f"  Cancelled Orders: {len(stuck_orders)}")

conn.close()
print("\nDone!")
