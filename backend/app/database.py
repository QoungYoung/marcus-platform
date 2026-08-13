# -*- coding: utf-8 -*-
"""
SQLAlchemy database engine & session management.
Uses synchronous SQLAlchemy — consistent with the existing codebase pattern.
"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import get_settings

settings = get_settings()

# 自动选择：Docker 内用 postgres 服务名，本地用 localhost
DATABASE_URL = settings.DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)

Base = declarative_base()


def get_db():
    """FastAPI 依赖：获取数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """创建所有表（如果不存在）+ 轻量级 schema 补丁（idempotent ALTER TABLE）"""
    # 确保所有模型被导入，让它们注册到 Base.metadata
    import app.models.prompt  # noqa: F401
    import app.models.stop_loss_log  # noqa: F401
    import app.models.fund_flow_cache  # noqa: F401
    import app.models.backtest_orm  # noqa: F401
    import app.models.market_orm  # noqa: F401
    import app.models.system_state  # noqa: F401
    import app.models.paper_trade  # noqa: F401
    import app.models.position_add_log  # noqa: F401
    import app.models.golden_pit  # noqa: F401
    import app.models.golden_pit_etf_config  # noqa: F401
    import app.models.golden_pit_dca_log  # noqa: F401
    import app.models.golden_pit_sector_config  # noqa: F401
    Base.metadata.create_all(bind=engine)
    # ── Schema 补丁：给已存在的表加新列（避免 SQLAlchemy create_all 漏 ALTER） ──
    _apply_schema_patches()


def _apply_schema_patches():
    """idempotent 的列补丁。每次启动检查一次。"""
    from sqlalchemy import text, inspect
    patches = [
        # (table, column, column_def)
        ("backtest_equity_snapshots", "cost_value", "FLOAT DEFAULT 0"),
        ("backtest_equity_snapshots", "float_pnl", "FLOAT DEFAULT 0"),
        ("backtest_equity_snapshots", "baseline_return", "FLOAT DEFAULT 0"),
        ("backtest_equity_snapshots", "cost_based_asset", "FLOAT DEFAULT 0"),
        ("backtest_equity_snapshots", "daily_pct", "FLOAT DEFAULT 0"),
        ("backtest_equity_snapshots", "cost_based_return", "FLOAT DEFAULT 0"),
        ("backtest_trades", "stock_name", "VARCHAR(50) DEFAULT ''"),
        ("backtest_trades", "profit", "FLOAT DEFAULT 0"),
        ("backtest_trades", "profit_pct", "FLOAT DEFAULT 0"),
        ("backtest_tasks", "include_chinext", "BOOLEAN DEFAULT FALSE"),
        # 2026-06: 交易明细导出增强
        ("backtest_trades", "phase_time", "VARCHAR(5) DEFAULT ''"),
        ("backtest_trades", "signal_price", "FLOAT DEFAULT 0"),
        ("backtest_trades", "actual_price", "FLOAT DEFAULT 0"),
        ("backtest_trades", "stamp_tax", "FLOAT DEFAULT 0"),
        ("backtest_trades", "transfer_fee", "FLOAT DEFAULT 0"),
        ("backtest_trades", "slippage_pct", "FLOAT DEFAULT 0"),
        ("backtest_trades", "net_profit", "FLOAT DEFAULT 0"),
        # 2026-06: T+1 违规标记 (回测引擎历史 bug: set_current_date 缺失导致 T+0 违规)
        ("backtest_trades", "is_t0_violation", "BOOLEAN DEFAULT FALSE"),
        ("backtest_trades", "t0_violation_note", "VARCHAR(200) DEFAULT ''"),
        # 2026-06: 回测模型可配置化
        ("backtest_tasks", "model_name", "VARCHAR(50) DEFAULT 'deepseek-v4-flash'"),
        ("backtest_tasks", "thinking_level", "VARCHAR(20) DEFAULT 'high'"),
        ("golden_pit_snapshots", "change_5", "FLOAT DEFAULT NULL"),
        ("golden_pit_snapshots", "change_20", "FLOAT DEFAULT NULL"),
        # 2026-07: DCA v5 窗口进度追踪
        ("golden_pit_dca_log", "schedule_day", "INTEGER DEFAULT NULL"),
        ("golden_pit_dca_log", "trend_factor", "FLOAT DEFAULT NULL"),
        # 2026-08: VN.PY paper account 持仓列缺失导致无持仓报错
        ("paper_positions", "volume", "INTEGER DEFAULT 0"),
        ("paper_positions", "frozen", "INTEGER DEFAULT 0"),
        ("paper_positions", "avg_price", "DOUBLE PRECISION DEFAULT 0"),
    ]
    # ── 2026-08: 多账户模拟盘隔离（paper_accounts 注册表 + 6 张 paper 表 account_id 维度） ──
    _apply_paper_account_migration()

    # ── 2026-08: API/Worker 拆分控制通道 ──
    _apply_worker_control_migration()

    # (table, column, new_type) — ALTER COLUMN TYPE，用于已有列
    alter_patches = [
        # 2026-07-28: strategy 字段太短，tier/pos/trend 组合超 20 字符
        ("golden_pit_dca_log", "strategy", "VARCHAR(50)"),
        # 2026-08: 板块配置值改为 TEXT，容纳 dca_carrier JSON（fixed_combo 双标的大于 100 字符）
        ("golden_pit_sector_config", "config_value", "TEXT"),
    ]
    try:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        with engine.begin() as conn:
            for table, col, ddl in patches:
                if table not in existing_tables:
                    continue
                cols = {c["name"] for c in inspector.get_columns(table)}
                if col in cols:
                    continue
                # 跨方言用 ADD COLUMN IF NOT EXISTS（PostgreSQL/SQLite 都支持）
                sql = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ddl}"
                conn.execute(text(sql))
                print(f"[DB] PATCH: {table}.{col} ADD ({ddl})")
            for table, col, new_type in alter_patches:
                if table not in existing_tables:
                    continue
                cols = {c["name"]: c for c in inspector.get_columns(table)}
                if col not in cols:
                    continue
                current_type = str(cols[col]["type"]).upper()
                # 只当当前类型不同时才 ALTER
                if new_type.upper() in current_type:
                    continue
                sql = f"ALTER TABLE {table} ALTER COLUMN {col} TYPE {new_type}"
                conn.execute(text(sql))
                print(f"[DB] PATCH: {table}.{col} ALTER TYPE → {new_type}")
    except Exception as e:
        print(f"[DB] PATCH warn: {e}")


def _apply_worker_control_migration():
    """API/Worker 拆分：worker_status（状态快照）+ worker_commands（控制命令）。"""
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS worker_status (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    pid INTEGER,
                    hostname TEXT DEFAULT '',
                    heartbeat TIMESTAMPTZ NOT NULL DEFAULT now(),
                    snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            ))
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS worker_commands (
                    id BIGSERIAL PRIMARY KEY,
                    cmd TEXT NOT NULL,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    status TEXT NOT NULL DEFAULT 'pending',
                    result JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    done_at TIMESTAMPTZ
                )
                """
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_worker_commands_status ON worker_commands (status, id)"
            ))
        print("[DB] PATCH: worker_status / worker_commands 创建完成")
    except Exception as e:
        print(f"[DB] PATCH warn (worker tables): {e}")


def _apply_paper_account_migration():
    """多账户隔离的幂等迁移：paper 表加 account_id、重建复合主键、建注册表并播种。"""
    from sqlalchemy import text, inspect

    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())

        # 1) 各账本表增加 account_id 列（存量数据默认归入 stock 账户）
        account_col_tables = [
            "paper_orders", "paper_trades", "paper_positions",
            "paper_daily_snapshot", "paper_capital_adjustments",
        ]
        with engine.begin() as conn:
            for t in account_col_tables:
                if t not in tables:
                    continue
                conn.execute(text(
                    f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS account_id VARCHAR(16) NOT NULL DEFAULT 'stock'"
                ))
            # 2) paper_account_info：id → account_id 主键
            if "paper_account_info" in tables:
                conn.execute(text("ALTER TABLE paper_account_info DROP CONSTRAINT IF EXISTS paper_account_info_pkey"))
                conn.execute(text("ALTER TABLE paper_account_info DROP COLUMN IF EXISTS id"))
                conn.execute(text("ALTER TABLE paper_account_info ADD COLUMN IF NOT EXISTS account_id VARCHAR(16)"))
                conn.execute(text("UPDATE paper_account_info SET account_id = 'stock' WHERE account_id IS NULL"))
                conn.execute(text("ALTER TABLE paper_account_info ALTER COLUMN account_id SET NOT NULL"))
                conn.execute(text("ALTER TABLE paper_account_info ADD PRIMARY KEY (account_id)"))
            # 3) paper_positions：symbol 主键 → (account_id, symbol) 复合主键
            if "paper_positions" in tables:
                conn.execute(text("ALTER TABLE paper_positions DROP CONSTRAINT IF EXISTS paper_positions_pkey"))
                conn.execute(text("ALTER TABLE paper_positions ADD PRIMARY KEY (account_id, symbol)"))
            # 4) paper_daily_snapshot：trade_date 主键 → (account_id, trade_date) 复合主键
            if "paper_daily_snapshot" in tables:
                conn.execute(text("ALTER TABLE paper_daily_snapshot DROP CONSTRAINT IF EXISTS paper_daily_snapshot_pkey"))
                conn.execute(text("ALTER TABLE paper_daily_snapshot ADD PRIMARY KEY (account_id, trade_date)"))
            # 5) 账户索引
            for t, idx in [
                ("paper_orders", "idx_paper_orders_account"),
                ("paper_trades", "idx_paper_trades_account"),
                ("paper_capital_adjustments", "idx_paper_capital_account"),
            ]:
                if t in tables:
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS {idx} ON {t} (account_id)"))
            # 6) 注册表 + 种子
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS paper_accounts ("
                " account_id VARCHAR(16) PRIMARY KEY,"
                " name VARCHAR(50) NOT NULL,"
                " module VARCHAR(50) DEFAULT '',"
                " initial_capital DOUBLE PRECISION NOT NULL,"
                " enabled INTEGER DEFAULT 1,"
                " created_at TEXT NOT NULL)"
            ))
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(text(
                "INSERT INTO paper_accounts (account_id, name, module, initial_capital, enabled, created_at) "
                "SELECT 'stock', '股票模拟账户', 'stock_trading', 1000000, 1, :now "
                "WHERE NOT EXISTS (SELECT 1 FROM paper_accounts WHERE account_id = 'stock')"
            ), {"now": now})
            conn.execute(text(
                "INSERT INTO paper_accounts (account_id, name, module, initial_capital, enabled, created_at) "
                "SELECT 'golden_pit', '黄金坑 ETF 模拟账户', 'golden_pit_dca', 250000, 1, :now "
                "WHERE NOT EXISTS (SELECT 1 FROM paper_accounts WHERE account_id = 'golden_pit')"
            ), {"now": now})
            print("[DB] PATCH: paper 多账户迁移完成 (paper_accounts + account_id 维度)")
    except Exception as e:
        print(f"[DB] PATCH warn (paper multi-account): {e}")
