"""修复 trades.db 中 dot-format symbol (301566.SZ → SZ301566)"""
import sqlite3
import sys
from pathlib import Path

def fix_symbols(db_path: str, dry_run: bool = False):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    cursor = conn.cursor()

    # 查找所有 dot-format 的 symbol
    cursor.execute("SELECT DISTINCT symbol FROM trades WHERE symbol LIKE '%.SZ' OR symbol LIKE '%.SH' OR symbol LIKE '%.BJ'")
    bad_syms = [r[0] for r in cursor.fetchall()]

    if not bad_syms:
        print("没有需要修复的 symbol")
        conn.close()
        return

    print(f"找到 {len(bad_syms)} 个需要修复的 symbol: {bad_syms}")

    for old_sym in bad_syms:
        code, exchange = old_sym.split('.')
        new_sym = f"{exchange}{code}"
        print(f"  {old_sym} → {new_sym}")

        if dry_run:
            continue

        # 修复 trades 表
        cursor.execute("UPDATE trades SET symbol=? WHERE symbol=?", (new_sym, old_sym))
        trades_updated = cursor.rowcount

        # 修复 positions 表
        cursor.execute("UPDATE positions SET symbol=? WHERE symbol=?", (new_sym, old_sym))
        pos_updated = cursor.rowcount

        # 修复 orders 表
        cursor.execute("UPDATE orders SET symbol=? WHERE symbol=?", (new_sym, old_sym))
        orders_updated = cursor.rowcount

        print(f"    trades: {trades_updated} rows, positions: {pos_updated} rows, orders: {orders_updated} rows")

    conn.commit()
    conn.close()
    print("修复完成")

if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    db = Path(__file__).parent.parent / "backend" / "data" / "trades.db"
    if not db.exists():
        print(f"找不到数据库: {db}")
        sys.exit(1)
    fix_symbols(str(db), dry_run=dry)
