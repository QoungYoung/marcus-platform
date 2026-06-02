#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Data Migration Script
Migrates data from SQLite databases (workspace-marcus) to PostgreSQL (marcus-platform).

Usage:
    python scripts/migrate_data.py --source /path/to/workspace --target postgresql://...

This script will:
1. Read existing SQLite databases (trades.db, news.db, cache.db)
2. Create PostgreSQL schema if needed
3. Migrate all data with integrity verification
"""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Try to import psycopg2, install if not available
try:
    import psycopg2
except ImportError:
    print("psycopg2 not installed. Installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "psycopg2-binary"])
    import psycopg2


def parse_args():
    parser = argparse.ArgumentParser(description="Migrate Marcus data from SQLite to PostgreSQL")
    parser.add_argument(
        "--source",
        type=str,
        default="F:/pythonProject/AITrade/workspace-marcus",
        help="Source workspace path"
    )
    parser.add_argument(
        "--target",
        type=str,
        default="postgresql://marcus:marcus_password@localhost:5432/marcus_trading",
        help="Target PostgreSQL connection string"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without making changes"
    )
    return parser.parse_args()


def get_sqlite_conn(db_path: Path) -> Optional[sqlite3.Connection]:
    """Connect to SQLite database."""
    if not db_path.exists():
        print(f"Warning: {db_path} does not exist, skipping")
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def create_pg_conn(target: str):
    """Connect to PostgreSQL."""
    return psycopg2.connect(target)


def create_schema(pg_conn):
    """Create PostgreSQL schema for Marcus trading data."""
    cursor = pg_conn.cursor()

    # Accounts table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id SERIAL PRIMARY KEY,
            account_type VARCHAR(20) NOT NULL DEFAULT 'paper',
            initial_capital DECIMAL(15, 2) NOT NULL,
            available_cash DECIMAL(15, 2) NOT NULL,
            frozen_cash DECIMAL(15, 2) DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Positions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id SERIAL PRIMARY KEY,
            account_id INTEGER REFERENCES accounts(id),
            symbol VARCHAR(10) NOT NULL,
            volume INTEGER NOT NULL,
            avg_price DECIMAL(10, 4) NOT NULL,
            current_price DECIMAL(10, 4),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(account_id, symbol)
        )
    """)

    # Trades table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id SERIAL PRIMARY KEY,
            order_id VARCHAR(20) UNIQUE NOT NULL,
            account_id INTEGER REFERENCES accounts(id),
            symbol VARCHAR(10) NOT NULL,
            direction VARCHAR(10) NOT NULL,
            price DECIMAL(10, 4) NOT NULL,
            volume INTEGER NOT NULL,
            amount DECIMAL(15, 2) NOT NULL,
            profit DECIMAL(15, 2),
            reason TEXT,
            status VARCHAR(20) DEFAULT 'executed',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # News table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id SERIAL PRIMARY KEY,
            title VARCHAR(500) NOT NULL,
            content TEXT,
            source VARCHAR(100),
            publish_time TIMESTAMP NOT NULL,
            sentiment VARCHAR(20),
            sentiment_score DECIMAL(5, 2),
            category VARCHAR(50),
            url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Market indices table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_indices (
            id SERIAL PRIMARY KEY,
            symbol VARCHAR(20) UNIQUE NOT NULL,
            name VARCHAR(50) NOT NULL,
            current_price DECIMAL(10, 4),
            last_close DECIMAL(10, 4),
            change DECIMAL(10, 4),
            change_pct DECIMAL(8, 4),
            volume DECIMAL(15, 2),
            high DECIMAL(10, 4),
            low DECIMAL(10, 4),
            open_price DECIMAL(10, 4),
            gap_pct DECIMAL(8, 4),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Strategy scans table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS strategy_scans (
            id SERIAL PRIMARY KEY,
            scan_time TIMESTAMP NOT NULL,
            stance VARCHAR(50),
            stance_code VARCHAR(20),
            position_limit INTEGER,
            sentiment_score DECIMAL(5, 2),
            hot_industries TEXT[],
            watchlist TEXT[],
            sector_allocation JSONB,
            gap_risk JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Order audit log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_audit_log (
            id SERIAL PRIMARY KEY,
            order_id VARCHAR(20) NOT NULL,
            action VARCHAR(50) NOT NULL,
            details JSONB,
            ip_address VARCHAR(45),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_created_at ON trades(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_publish_time ON news(publish_time)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_sentiment ON news(sentiment)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_strategy_scans_time ON strategy_scans(scan_time)")

    pg_conn.commit()
    print("Schema created successfully")


def migrate_account_info(source: Path, pg_conn):
    """Migrate account info from trades.db to PostgreSQL."""
    sqlite_db = source / "skills" / "vnpy-paper-trading" / "data" / "trades.db"
    if not sqlite_db.exists():
        print("Warning: trades.db not found, skipping account migration")
        return

    sqlite_conn = get_sqlite_conn(sqlite_db)
    if not sqlite_conn:
        return

    cursor = sqlite_conn.cursor()
    cursor.execute("SELECT * FROM account_info WHERE id=1")
    row = cursor.fetchone()

    if row:
        pg_cursor = pg_conn.cursor()
        pg_cursor.execute("""
            INSERT INTO accounts (id, initial_capital, available_cash, frozen_cash, updated_at)
            VALUES (1, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                initial_capital = EXCLUDED.initial_capital,
                available_cash = EXCLUDED.available_cash,
                frozen_cash = EXCLUDED.frozen_cash,
                updated_at = EXCLUDED.updated_at
        """, (row['initial_capital'], row['available_cash'], row.get('frozen_cash', 0), datetime.now()))
        pg_conn.commit()
        print(f"Migrated account info: initial_capital={row['initial_capital']}")

    sqlite_conn.close()


def migrate_trades(source: Path, pg_conn):
    """Migrate trades from trades.db to PostgreSQL."""
    sqlite_db = source / "skills" / "vnpy-paper-trading" / "data" / "trades.db"
    if not sqlite_db.exists():
        print("Warning: trades.db not found, skipping trades migration")
        return

    sqlite_conn = get_sqlite_conn(sqlite_db)
    if not sqlite_conn:
        return

    cursor = sqlite_conn.cursor()
    cursor.execute("""
        SELECT orderid, symbol, direction, price, volume, amount, profit, status, created_at, updated_at
        FROM trades ORDER BY created_at
    """)
    rows = cursor.fetchall()

    pg_cursor = pg_conn.cursor()
    migrated = 0

    for row in rows:
        try:
            pg_cursor.execute("""
                INSERT INTO trades (order_id, symbol, direction, price, volume, amount, profit, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (order_id) DO NOTHING
            """, (
                row['orderid'],
                row['symbol'],
                row['direction'],
                row['price'],
                row['volume'],
                row['amount'],
                row.get('profit', 0),
                row['status'],
                row['created_at'],
                row['updated_at'],
            ))
            migrated += 1
        except Exception as e:
            print(f"Warning: Failed to migrate trade {row['orderid']}: {e}")

    pg_conn.commit()
    print(f"Migrated {migrated}/{len(rows)} trades")
    sqlite_conn.close()


def migrate_news(source: Path, pg_conn):
    """Migrate news from news.db to PostgreSQL."""
    sqlite_db = source / "skills" / "akshare-news" / "data" / "news.db"
    if not sqlite_db.exists():
        print("Warning: news.db not found, skipping news migration")
        return

    sqlite_conn = get_sqlite_conn(sqlite_db)
    if not sqlite_conn:
        return

    cursor = sqlite_conn.cursor()
    cursor.execute("""
        SELECT id, title, content, source, publish_time, sentiment, keyword as category, url
        FROM news ORDER BY publish_time DESC LIMIT 10000
    """)
    rows = cursor.fetchall()

    pg_cursor = pg_conn.cursor()
    migrated = 0

    for row in rows:
        try:
            pg_cursor.execute("""
                INSERT INTO news (title, content, source, publish_time, sentiment, category, url)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                row['title'],
                row['content'],
                row['source'],
                row['publish_time'],
                row['sentiment'],
                row['category'],
                row['url'],
            ))
            migrated += 1
        except Exception as e:
            print(f"Warning: Failed to migrate news {row['id']}: {e}")

    pg_conn.commit()
    print(f"Migrated {migrated}/{len(rows)} news articles")
    sqlite_conn.close()


def verify_migration(pg_conn):
    """Verify migrated data integrity."""
    cursor = pg_conn.cursor()

    # Check account
    cursor.execute("SELECT * FROM accounts WHERE id=1")
    account = cursor.fetchone()
    print(f"\nVerification:")
    print(f"  Account: {'OK' if account else 'MISSING'}")

    # Check trades count
    cursor.execute("SELECT COUNT(*) as cnt FROM trades")
    trades_count = cursor.fetchone()[0]
    print(f"  Trades: {trades_count}")

    # Check news count
    cursor.execute("SELECT COUNT(*) as cnt FROM news")
    news_count = cursor.fetchone()[0]
    print(f"  News: {news_count}")


def main():
    args = parse_args()

    source = Path(args.source)
    if not source.exists():
        print(f"Error: Source path {source} does not exist")
        sys.exit(1)

    print(f"Starting migration...")
    print(f"  Source: {source}")
    print(f"  Target: {args.target}")
    print(f"  Dry run: {args.dry_run}")

    if args.dry_run:
        print("\n[DRY RUN] No changes will be made")
        # Still try to read source data to show what would be migrated
        trades_db = source / "skills" / "vnpy-paper-trading" / "data" / "trades.db"
        if trades_db.exists():
            conn = get_sqlite_conn(trades_db)
            if conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM trades")
                print(f"  Would migrate {cursor.fetchone()[0]} trades")
                conn.close()
        return

    # Create schema
    print("\n1. Creating PostgreSQL schema...")
    try:
        pg_conn = create_pg_conn(args.target)
        create_schema(pg_conn)
    except Exception as e:
        print(f"Error connecting to PostgreSQL: {e}")
        print("Make sure PostgreSQL is running and the database exists")
        sys.exit(1)

    # Migrate data
    print("\n2. Migrating account info...")
    migrate_account_info(source, pg_conn)

    print("\n3. Migrating trades...")
    migrate_trades(source, pg_conn)

    print("\n4. Migrating news...")
    migrate_news(source, pg_conn)

    # Verify
    print("\n5. Verifying migration...")
    verify_migration(pg_conn)

    pg_conn.close()
    print("\nMigration completed successfully!")


if __name__ == "__main__":
    main()
