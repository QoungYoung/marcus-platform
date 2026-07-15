# -*- coding: utf-8 -*-
"""
SQLAlchemy database engine & session management.
Uses synchronous SQLAlchemy — consistent with the existing codebase pattern.
"""
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
        ("backtest_tasks", "model_name", "VARCHAR(50) DEFAULT 'deepseek-v4-pro'"),
        ("backtest_tasks", "thinking_level", "VARCHAR(20) DEFAULT 'high'"),
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
    except Exception as e:
        print(f"[DB] PATCH warn: {e}")
