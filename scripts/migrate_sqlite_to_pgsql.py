#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite → PostgreSQL 数据迁移脚本
将 trades.db 中的 paper trading 数据迁移到 PostgreSQL paper_* 表。

用法:
    python scripts/migrate_sqlite_to_pgsql.py              # 执行迁移
    python scripts/migrate_sqlite_to_pgsql.py --dry-run    # 仅打印行数，不写入
    python scripts/migrate_sqlite_to_pgsql.py --db /path/to/trades.db  # 指定 SQLite 路径
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
import psycopg2.extensions

# ── 默认路径 ──
DEFAULT_SQLITE_DB = Path(__file__).parent.parent / "backend" / "data" / "trades.db"
DEFAULT_PG_URL = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@localhost:5432/marcus_trading")

# ── 表映射：SQLite 表名 → (PostgreSQL 表名, 主键列, 需要映射的列) ──
TABLE_MAP = {
    "orders": {
        "pg_table": "paper_orders",
        "pk": "orderid",
        "columns": ["orderid", "symbol", "direction", "price", "volume", "status",
                     "traded", "created_at", "updated_at", "reason"],
    },
    "trades": {
        "pg_table": "paper_trades",
        "pk": "id",
        "columns": ["id", "orderid", "symbol", "direction", "price", "volume",
                     "amount", "profit", "created_at", "trade_date", "voided",
                     "void_reason", "voided_at", "reason"],
    },
    "positions": {
        "pg_table": "paper_positions",
        "pk": "symbol",
        "columns": ["symbol", "entry_date", "highest_price", "updated_at"],
    },
    "account_info": {
        "pg_table": "paper_account_info",
        "pk": "id",
        "columns": ["id", "initial_capital", "available_cash", "frozen_cash",
                     "order_counter", "updated_at"],
    },
    "daily_snapshot": {
        "pg_table": "paper_daily_snapshot",
        "pk": "trade_date",
        "columns": ["trade_date", "total_asset", "available_cash", "frozen_cash",
                     "position_value", "cost_value", "realized_pnl", "float_pnl",
                     "total_pnl", "initial_capital", "created_at"],
    },
    "long_term_candidates": {
        "pg_table": "long_term_candidates",
        "pk": "id",
        "columns": ["id", "symbol", "name", "status", "chain_name", "chain_role",
                     "notes", "added_at", "promoted_at", "last_checked_at",
                     "last_grade", "checks_count"],
    },
}


def parse_pg_url(url: str) -> dict:
    """从 DATABASE_URL 解析 PostgreSQL 连接参数。"""
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "dbname": (parsed.path or "/marcus_trading").lstrip("/"),
        "user": parsed.username or "marcus",
        "password": parsed.password or "marcus123",
    }


def connect_sqlite(db_path: str) -> sqlite3.Connection:
    if not Path(db_path).exists():
        print(f"[ERROR] SQLite 数据库不存在: {db_path}")
        sys.exit(1)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def connect_pg(params: dict):
    return psycopg2.connect(**params)


def main():
    parser = argparse.ArgumentParser(description="SQLite → PostgreSQL 数据迁移")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅打印将要迁移的行数，不实际写入")
    parser.add_argument("--db", type=str, default=str(DEFAULT_SQLITE_DB),
                        help=f"SQLite 数据库路径 (默认: {DEFAULT_SQLITE_DB})")
    parser.add_argument("--pg-url", type=str, default=DEFAULT_PG_URL,
                        help="PostgreSQL 连接 URL")
    parser.add_argument("--skip-confirm", action="store_true",
                        help="跳过确认提示（非交互模式）")
    args = parser.parse_args()

    # ── 连接数据库 ──
    sqlite_conn = connect_sqlite(args.db)
    pg_params = parse_pg_url(args.pg_url)
    pg_conn = connect_pg(pg_params)
    pg_conn.autocommit = False

    try:
        # ── 统计 ──
        print("=" * 60)
        print("SQLite → PostgreSQL 数据迁移")
        print("=" * 60)
        print(f"源 (SQLite):  {args.db}")
        print(f"目标 (PG):    {pg_params['host']}:{pg_params['port']}/{pg_params['dbname']}")
        print(f"模式:         {'DRY-RUN (仅统计)' if args.dry_run else '正式迁移'}")
        print()

        counts = {}
        for sqlite_table, cfg in TABLE_MAP.items():
            cursor = sqlite_conn.cursor()
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {sqlite_table}")
                cnt = cursor.fetchone()[0]
            except sqlite3.OperationalError:
                cnt = 0
            counts[sqlite_table] = cnt
            print(f"  {sqlite_table}: {cnt} 行 → {cfg['pg_table']}")

        total = sum(counts.values())
        print(f"\n  总计: {total} 行")
        print()

        if args.dry_run:
            print("[DRY-RUN] 仅统计，未写入任何数据。")
            return

        if total == 0:
            print("没有数据需要迁移。")
            return

        # ── 确认 ──
        if not args.skip_confirm:
            print("⚠️  将清空目标表并写入以上数据。")
            resp = input("确认继续? [y/N]: ").strip().lower()
            if resp not in ("y", "yes"):
                print("已取消。")
                return

        # ── 迁移 ──
        pg_cursor = pg_conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for sqlite_table, cfg in TABLE_MAP.items():
            pg_table = cfg["pg_table"]
            columns = cfg["columns"]
            col_placeholders = ", ".join(["%s"] * len(columns))
            col_names = ", ".join(columns)

            # 清空目标表（幂等）
            pg_cursor.execute(f"DELETE FROM {pg_table}")
            print(f"[CLEAR] {pg_table} 已清空")

            # 读取 SQLite 数据
            sqlite_cursor = sqlite_conn.cursor()
            try:
                sqlite_cursor.execute(f"SELECT {col_names} FROM {sqlite_table}")
            except sqlite3.OperationalError as e:
                print(f"[SKIP] {sqlite_table}: {e}")
                continue

            # 写入 PostgreSQL
            inserted = 0
            for row in sqlite_cursor.fetchall():
                pg_cursor.execute(
                    f"INSERT INTO {pg_table} ({col_names}) VALUES ({col_placeholders})",
                    tuple(row)
                )
                inserted += 1

            pg_conn.commit()
            print(f"[OK] {pg_table}: {inserted} 行已写入")

        # ── 重置序列（PostgreSQL auto-increment） ──
        pg_cursor.execute("SELECT setval('paper_trades_id_seq', COALESCE((SELECT MAX(id) FROM paper_trades), 1))")
        pg_conn.commit()

        print(f"\n✅ 迁移完成 ({now})")
        print(f"   旧 SQLite 文件未删除，保留作为备份: {args.db}")

    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    main()
