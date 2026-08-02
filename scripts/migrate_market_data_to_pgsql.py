#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite -> PostgreSQL 迁移脚本（市场主数据）

将以下 SQLite 数据迁移到 PostgreSQL：
- data/stock_pool.db : stock_pool / sectors / concept_sectors / stock_concept_map / stock_sector_map
- data/cache.db      : etf_pool
- data/trades.db     : sector_config（迁移后 DROP 已废弃的 watchlist 表）

用法:
    python scripts/migrate_market_data_to_pgsql.py              # 执行迁移
    python scripts/migrate_market_data_to_pgsql.py --dry-run    # 仅打印行数，不写入
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PG_URL = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@localhost:5432/marcus_trading")

# (sqlite 文件, sqlite 表名, pg 表名, 列清单)
SOURCES = [
    (ROOT / "data" / "stock_pool.db", "stock_pool", "stock_pool",
     ["ts_code", "symbol", "name", "area", "industry", "market", "list_date",
      "is_st", "market_cap", "updated_at", "board"]),
    (ROOT / "data" / "stock_pool.db", "sectors", "sectors",
     ["id", "sector_name", "sector_type", "stock_count", "updated_at"]),
    (ROOT / "data" / "stock_pool.db", "concept_sectors", "concept_sectors",
     ["id", "concept_name", "keywords", "updated_at"]),
    (ROOT / "data" / "stock_pool.db", "stock_concept_map", "stock_concept_map",
     ["ts_code", "concept_name"]),
    (ROOT / "data" / "stock_pool.db", "stock_sector_map", "stock_sector_map",
     ["ts_code", "sector_name"]),
    (ROOT / "data" / "cache.db", "etf_pool", "etf_pool",
     ["symbol", "name", "sector", "catalyst_type", "priority", "data", "updated_at"]),
    (ROOT / "data" / "trades.db", "sector_config", "sector_config",
     ["sector_key", "name", "indices", "etfs", "weight", "stocks", "updated_at", "etf_codes"]),
]

# 迁移后需要重置自增序列的表（PG serial/identity）
SEQUENCE_TABLES = ["sectors", "concept_sectors"]

WATCHLIST_DB = ROOT / "data" / "trades.db"


def parse_pg_url(url: str) -> dict:
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "dbname": (parsed.path or "/marcus_trading").lstrip("/"),
        "user": parsed.username or "marcus",
        "password": parsed.password or "marcus123",
    }


def connect_sqlite(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        print(f"[ERROR] SQLite 数据库不存在: {db_path}")
        sys.exit(1)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def source_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def main():
    parser = argparse.ArgumentParser(description="SQLite -> PostgreSQL 市场主数据迁移")
    parser.add_argument("--dry-run", action="store_true", help="仅统计行数，不写入")
    parser.add_argument("--pg-url", type=str, default=DEFAULT_PG_URL, help="PostgreSQL 连接 URL")
    parser.add_argument("--skip-confirm", action="store_true", help="跳过确认提示（非交互模式）")
    args = parser.parse_args()

    pg_params = parse_pg_url(args.pg_url)

    print("=" * 60)
    print("SQLite -> PostgreSQL 市场主数据迁移")
    print("=" * 60)
    print(f"目标 (PG): {pg_params['host']}:{pg_params['port']}/{pg_params['dbname']}")
    print(f"模式: {'DRY-RUN (仅统计)' if args.dry_run else '正式迁移'}")
    print()

    # ---- 统计源行数 ----
    counts = {}
    for db_path, table, pg_table, _cols in SOURCES:
        conn = connect_sqlite(db_path)
        counts[(db_path.name, table)] = source_count(conn, table)
        conn.close()
        print(f"  {db_path.name}.{table}: {counts[(db_path.name, table)]} 行 -> {pg_table}")

    wl_conn = connect_sqlite(WATCHLIST_DB)
    watchlist_cnt = source_count(wl_conn, "watchlist")
    wl_conn.close()
    print(f"  trades.db.watchlist: {watchlist_cnt} 行（迁移后 DROP，不迁移数据）")

    total = sum(counts.values())
    print(f"\n  总计待迁移: {total} 行")
    if args.dry_run:
        print("\n[DRY-RUN] 仅统计，未写入任何数据。")
        return

    if total == 0:
        print("没有数据需要迁移。")
        return

    if not args.skip_confirm:
        print("\n将清空目标表并写入以上数据，同时 DROP trades.db 的 watchlist 表。")
        resp = input("确认继续? [y/N]: ").strip().lower()
        if resp not in ("y", "yes"):
            print("已取消。")
            return

    pg_conn = psycopg2.connect(**pg_params)
    pg_conn.autocommit = False
    try:
        pg_cursor = pg_conn.cursor()

        for db_path, table, pg_table, cols in SOURCES:
            col_names = ", ".join(cols)
            placeholders = ", ".join(["%s"] * len(cols))

            # 清空目标表
            pg_cursor.execute(f"DELETE FROM {pg_table}")
            print(f"\n[CLEAR] {pg_table} 已清空")

            conn = connect_sqlite(db_path)
            try:
                rows = conn.execute(f"SELECT {col_names} FROM {table}").fetchall()
            except sqlite3.OperationalError as e:
                print(f"[SKIP] {db_path.name}.{table}: {e}")
                conn.close()
                continue

            inserted = 0
            if rows:
                # 批量插入：避免在 SSH 隧道上逐行往返（executemany 会单行发送）
                from psycopg2.extras import execute_values
                batch_size = 5000
                row_list = [tuple(r) for r in rows]
                for i in range(0, len(row_list), batch_size):
                    chunk = row_list[i:i + batch_size]
                    execute_values(
                        pg_cursor,
                        f"INSERT INTO {pg_table} ({col_names}) VALUES %s",
                        chunk,
                        page_size=len(chunk),
                    )
                    inserted += len(chunk)
                    print(f"    {pg_table}: {inserted}/{len(rows)} 行")
            conn.close()
            pg_conn.commit()

            # 校验 PG 行数
            pg_cursor.execute(f"SELECT COUNT(*) FROM {pg_table}")
            pg_cnt = pg_cursor.fetchone()[0]
            print(f"[OK] {pg_table}: {inserted} 行已写入，PG 校验 {pg_cnt} 行"
                  + ("  (一致)" if inserted == pg_cnt else "  (不一致!)"))

        # 重置自增序列
        for table in SEQUENCE_TABLES:
            pg_cursor.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
            )
        pg_conn.commit()

        # 删除 trades.db 废弃的 watchlist 表
        wl_conn = connect_sqlite(WATCHLIST_DB)
        try:
            wl_conn.execute("DROP TABLE IF EXISTS watchlist")
            wl_conn.commit()
            print(f"\n[DROP] trades.db watchlist 已删除（原 {watchlist_cnt} 行，未迁移）")
        finally:
            wl_conn.close()

        print("\n迁移完成。SQLite 源文件保留作备份。")

    finally:
        pg_conn.close()


if __name__ == "__main__":
    main()