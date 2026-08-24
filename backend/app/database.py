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

    # ── 2026-08: 做T专用账户（t account + 五张 t_* 表） ──
    _apply_t_account_migration()

    # ── 2026-08: 做T底仓建仓（t_build_events 审计 + t_build_params 参数） ──
    _apply_t_build_migration()

    # ── 2026-08: 做T回测（t_backtest_* 任务/事件/成交/权益/指标） ──
    _apply_t_backtest_migration()

    # ── 2026-08: V反 全市场日线基础数据落库（t_vreb_daily，扫描增量） ──
    _apply_vreb_daily_migration()

    # ── 2026-08: AI 主导做T（t_ai_actions 审计 + t_conditions 发布者/会话） ──
    _apply_ai_led_migration()

    # ── 2026-08: API/Worker 拆分控制通道 ──
    _apply_worker_control_migration()

    # (table, column, new_type) — ALTER COLUMN TYPE，用于已有列
    alter_patches = [
        # 2026-07-28: strategy 字段太短，tier/pos/trend 组合超 20 字符
        ("golden_pit_dca_log", "strategy", "VARCHAR(50)"),
        # 2026-08-13: 全行业轨 DCA 日志 fund_code=industry/<id> 超 VARCHAR(10)
        ("golden_pit_dca_log", "fund_code", "VARCHAR(30)"),
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
            # 7) paper_account_info 账本表兜底创建 + 从注册表播种：
            #    消除 VNPyBridge 读取不到账本时落回 10w 默认值、与 calc_position/portfolio
            #    （读注册表/账本，约 100w+）口径分裂导致的误拒（如 941x100=9.4w 被 40%x10w 拦截）。
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS paper_account_info ("
                " account_id VARCHAR(16) PRIMARY KEY,"
                " initial_capital DOUBLE PRECISION NOT NULL,"
                " seed_initial_capital DOUBLE PRECISION,"
                " available_cash DOUBLE PRECISION NOT NULL,"
                " frozen_cash DOUBLE PRECISION NOT NULL DEFAULT 0,"
                " order_counter INTEGER NOT NULL DEFAULT 0,"
                " updated_at TEXT NOT NULL)"
            ))
            conn.execute(text(
                "INSERT INTO paper_account_info "
                " (account_id, initial_capital, available_cash, frozen_cash, order_counter, updated_at) "
                "SELECT p.account_id, p.initial_capital, p.initial_capital, 0, 0, :now "
                "FROM paper_accounts p "
                "WHERE p.enabled = 1 "
                "  AND NOT EXISTS (SELECT 1 FROM paper_account_info i WHERE i.account_id = p.account_id)"
            ), {"now": now})
            # 8) seed_initial_capital：真实历史种子，权益曲线回放用（不被资金调整改动）
            conn.execute(text(
                "ALTER TABLE paper_account_info ADD COLUMN IF NOT EXISTS seed_initial_capital DOUBLE PRECISION"
            ))
            conn.execute(text(
                "UPDATE paper_account_info SET seed_initial_capital = initial_capital WHERE seed_initial_capital IS NULL"
            ))
            print("[DB] PATCH: paper 多账户迁移完成 (paper_accounts + account_id 维度 + account_info 播种)")
    except Exception as e:
        print(f"[DB] PATCH warn (paper multi-account): {e}")


def _apply_t_account_migration():
    """做T专用账户迁移（幂等）：注册 t 账户 + 建 t_conditions/t_triggers/t_regime_state/t_daily_state/t_risk_state 五张表。

    对齐 _apply_paper_account_migration 范式：account_id 维度隔离、幂等可重跑。
    """
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            # 1) t 账户注册进 paper_accounts（独立资金，与 stock/golden_pit 隔离）
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(text(
                "INSERT INTO paper_accounts (account_id, name, module, initial_capital, enabled, created_at) "
                "SELECT 't', '做T专用账户', 't_account_trading', 200000, 1, :now "
                "WHERE NOT EXISTS (SELECT 1 FROM paper_accounts WHERE account_id = 't')"
            ), {"now": now})

            # 2) t_conditions — 做T条件注册表（条件元组 + 状态机 + regime_gate）
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS t_conditions (
                    id BIGSERIAL PRIMARY KEY,
                    account_id VARCHAR(16) NOT NULL DEFAULT 't',
                    symbol VARCHAR(16) NOT NULL,
                    trade_date VARCHAR(8) NOT NULL,
                    trigger_kind VARCHAR(20) NOT NULL DEFAULT 'low_buy',
                    target_price DOUBLE PRECISION,
                    reinform_price DOUBLE PRECISION,
                    vol_ratio_thresh DOUBLE PRECISION,
                    benchmark_turnover_profile JSONB,
                    stabilize_level VARCHAR(20),
                    sell_target_price DOUBLE PRECISION,
                    stop_loss_price DOUBLE PRECISION,
                    time_stop_open VARCHAR(8),
                    time_stop_close VARCHAR(8),
                    start_time VARCHAR(8),
                    end_time VARCHAR(8),
                    armed INTEGER DEFAULT 1,
                    armed_at TIMESTAMP,
                    last_triggered_at TIMESTAMP,
                    trigger_count_today INTEGER DEFAULT 0,
                    regime_gate VARCHAR(12) DEFAULT 'ALLOWED',
                    expression JSONB,
                    status VARCHAR(16) DEFAULT 'active',
                    created_at TIMESTAMP NOT NULL DEFAULT now(),
                    UNIQUE (account_id, symbol, trigger_kind, trade_date)
                )
                """
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_t_conditions_active ON t_conditions (account_id, status, trade_date)"
            ))
            # 迭代#58：条件方向字段（custom 等自由类型显式声明 buy/sell；
            # 空 = 按 trigger_kind 默认：low_buy/panic_vibrate→买，其余→卖）
            conn.execute(text(
                "ALTER TABLE t_conditions ADD COLUMN IF NOT EXISTS direction VARCHAR(8) DEFAULT ''"
            ))

            # 3) t_triggers — 做T触发事件流（状态机 + snapshot + 原子消费）
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS t_triggers (
                    id BIGSERIAL PRIMARY KEY,
                    account_id VARCHAR(16) NOT NULL DEFAULT 't',
                    condition_id BIGINT,
                    symbol VARCHAR(16) NOT NULL,
                    event_type VARCHAR(20) NOT NULL DEFAULT 'low_buy',
                    trigger_price DOUBLE PRECISION,
                    quote_price DOUBLE PRECISION,
                    suggest_bid_price DOUBLE PRECISION,
                    suggest_ask_price DOUBLE PRECISION,
                    slippage_budget DOUBLE PRECISION,
                    snapshot JSONB,
                    status VARCHAR(16) NOT NULL DEFAULT 'pending',
                    mode VARCHAR(12) NOT NULL DEFAULT 'auto',
                    reason VARCHAR(256),
                    claimed_by VARCHAR(64),
                    claimed_at TIMESTAMP,
                    executed_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT now(),
                    UNIQUE (condition_id, created_at)
                )
                """
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_t_triggers_pending ON t_triggers (account_id, status, id)"
            ))

            # 4) t_regime_state — 环境闸门状态（每交易日 1 行）
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS t_regime_state (
                    trade_date VARCHAR(8) PRIMARY KEY,
                    regime VARCHAR(10) NOT NULL DEFAULT 'ACTIVE',
                    daily_source VARCHAR(30) DEFAULT 'market_diagnosis',
                    updated_at_daily TIMESTAMP,
                    intraday_lowbias BOOLEAN DEFAULT FALSE,
                    intraday_index_drop DOUBLE PRECISION DEFAULT 0,
                    intraday_updated TIMESTAMP,
                    gate_low_buy VARCHAR(12) NOT NULL DEFAULT 'ALLOWED',
                    gate_high_sell VARCHAR(12) NOT NULL DEFAULT 'ALLOWED',
                    gate_interpret_sign INTEGER DEFAULT 1
                )
                """
            ))

            # 5) t_daily_state — 做T日级账本（累计回转额/净回转头寸/熔断）
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS t_daily_state (
                    account_id VARCHAR(16) NOT NULL DEFAULT 't',
                    trade_date VARCHAR(8) NOT NULL,
                    daily_turnover_amount DOUBLE PRECISION DEFAULT 0,
                    net_turnover_shares INTEGER DEFAULT 0,
                    realized_pnl DOUBLE PRECISION DEFAULT 0,
                    buy_count INTEGER DEFAULT 0,
                    sell_count INTEGER DEFAULT 0,
                    risk_breaker BOOLEAN DEFAULT FALSE,
                    breaker_reason VARCHAR(256),
                    updated_at TIMESTAMP NOT NULL DEFAULT now(),
                    PRIMARY KEY (account_id, trade_date)
                )
                """
            ))

            # 6) t_risk_state — 做T全局风控状态（STOP_ALL/连续亏损/档位）
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS t_risk_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    stop_all BOOLEAN DEFAULT FALSE,
                    regime VARCHAR(10) DEFAULT 'ACTIVE',
                    consecutive_losses INTEGER DEFAULT 0,
                    manual_lock BOOLEAN DEFAULT FALSE,
                    lock_reason VARCHAR(256),
                    updated_at TIMESTAMP NOT NULL DEFAULT now()
                )
                """
            ))
            conn.execute(text(
                "INSERT INTO t_risk_state (id, stop_all, regime, consecutive_losses) "
                "SELECT 1, FALSE, 'ACTIVE', 0 WHERE NOT EXISTS (SELECT 1 FROM t_risk_state WHERE id = 1)"
            ))

            # 7) 自由表达式列补丁（幂等：已有表补 expression 列）
            conn.execute(text(
                "ALTER TABLE t_conditions ADD COLUMN IF NOT EXISTS expression JSONB"
            ))
            # 8) trigger_kind 扩宽（VARCHAR(20)→50，Agent 自由命名条件类型）
            conn.execute(text(
                "ALTER TABLE t_conditions ALTER COLUMN trigger_kind TYPE VARCHAR(50)"
            ))

        print("[DB] PATCH: 做T账户迁移完成 (t account + t_conditions/t_triggers/t_regime_state/t_daily_state/t_risk_state)")
    except Exception as e:
        print(f"[DB] PATCH warn (t account tables): {e}")


def _apply_t_build_migration():
    """做T底仓建仓迁移（幂等）：t_build_events 建仓审计表 + t_build_params 建仓参数表。

    对齐 _apply_t_account_migration 范式：account_id='t' 维度、幂等可重跑。
    """
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            # 1) t_build_events — 底仓建仓审计/状态流（独立于 t_triggers 做T事件流）
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS t_build_events (
                    id BIGSERIAL PRIMARY KEY,
                    account_id VARCHAR(16) NOT NULL DEFAULT 't',
                    symbol VARCHAR(16) NOT NULL,
                    event_type VARCHAR(32) NOT NULL DEFAULT 'build_position',
                    side VARCHAR(8) NOT NULL DEFAULT 'buy',
                    price DOUBLE PRECISION,
                    volume INTEGER,
                    amount DOUBLE PRECISION,
                    executed_price DOUBLE PRECISION,
                    decision_source VARCHAR(16) NOT NULL DEFAULT 'agent',
                    reason VARCHAR(512),
                    regime VARCHAR(10),
                    gateway_result JSONB,
                    position_before JSONB,
                    position_after JSONB,
                    status VARCHAR(16) NOT NULL DEFAULT 'pending_confirmation',
                    created_at TIMESTAMP NOT NULL DEFAULT now(),
                    updated_at TIMESTAMP NOT NULL DEFAULT now()
                )
                """
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_t_build_events_symbol ON t_build_events (account_id, symbol, created_at)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_t_build_events_status ON t_build_events (account_id, status, id)"
            ))
            # 3) t_build_scan_results — 每日自动选股结果（盘后选股 → 次日自动建仓候选）
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS t_build_scan_results (
                    id BIGSERIAL PRIMARY KEY,
                    trade_date VARCHAR(10) NOT NULL,
                    symbol VARCHAR(16) NOT NULL,
                    score DOUBLE PRECISION,
                    reasons JSONB NOT NULL DEFAULT '[]',
                    trend VARCHAR(256),
                    status VARCHAR(16) NOT NULL DEFAULT 'pending',
                    built_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT now()
                )
                """
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_t_build_scan_date ON t_build_scan_results (trade_date, status)"
            ))
            # source 列：trend_break 日频入池来源标记（隔离于 stock 扫描），幂等
            conn.execute(text(
                "ALTER TABLE t_build_scan_results ADD COLUMN IF NOT EXISTS source VARCHAR(16) DEFAULT 'scan'"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_t_build_scan_source ON t_build_scan_results (source, status)"
            ))

            # 2) t_build_params — 建仓策略参数（分档初值，P4 敏感度扫描后固化）
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS t_build_params (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    params_json JSONB NOT NULL DEFAULT '{}',
                    updated_at TIMESTAMP NOT NULL DEFAULT now()
                )
                """
            ))
            conn.execute(text(
                "INSERT INTO t_build_params (id, params_json) "
                "SELECT 1, '{}' WHERE NOT EXISTS (SELECT 1 FROM t_build_params WHERE id = 1)"
            ))

        print("[DB] PATCH: 做T建仓迁移完成 (t_build_events + t_build_params)")
    except Exception as e:
        print(f"[DB] PATCH warn (t build tables): {e}")


def _apply_vreb_daily_migration():
    """V反 全市场日线基础数据落库（幂等）：t_vreb_daily。

    盘后全市场扫描基础数据（近 40 个交易日 OHLCV + 当日市值 + ST 标记）落库，
    次日扫描只增量拉 1 个交易日，避免每天重复拉 40 天全市场（tushare 调用 80+ 次 -> 2 次）。
    """
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS t_vreb_daily (
                    ts_code VARCHAR(16) NOT NULL,
                    trade_date DATE NOT NULL,
                    open DOUBLE PRECISION,
                    high DOUBLE PRECISION,
                    low DOUBLE PRECISION,
                    close DOUBLE PRECISION,
                    vol DOUBLE PRECISION,
                    total_mv DOUBLE PRECISION,
                    is_st BOOLEAN NOT NULL DEFAULT FALSE,
                    PRIMARY KEY (ts_code, trade_date)
                )
                """
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_t_vreb_daily_date ON t_vreb_daily (trade_date)"
            ))
        print("[DB] PATCH: V反全市场日线落库表完成 (t_vreb_daily)")
    except Exception as e:
        print(f"[DB] PATCH warn (vreb daily): {e}")


def _apply_t_backtest_migration():
    """做T回测迁移（幂等，对齐 _apply_t_build_migration 范式）：
    t_backtest_tasks / t_backtest_events / t_backtest_trades /
    t_backtest_equity_snapshots / t_backtest_metrics。
    """
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            # 1) 任务
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS t_backtest_tasks (
                    id BIGSERIAL PRIMARY KEY,
                    symbol VARCHAR(16) NOT NULL,
                    start_date VARCHAR(10),
                    end_date VARCHAR(10),
                    init_shares INTEGER NOT NULL DEFAULT 1000,
                    init_price DOUBLE PRECISION,
                    net_asset DOUBLE PRECISION NOT NULL DEFAULT 200000,
                    review_mode VARCHAR(10) NOT NULL DEFAULT 'llm',
                    conditions_json JSONB NOT NULL DEFAULT '[]',
                    status VARCHAR(16) NOT NULL DEFAULT 'pending',
                    progress INTEGER NOT NULL DEFAULT 0,
                    error_message VARCHAR(1024),
                    created_at TIMESTAMP NOT NULL DEFAULT now(),
                    started_at TIMESTAMP,
                    finished_at TIMESTAMP
                )
                """
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_t_backtest_tasks_status ON t_backtest_tasks (status, id)"
            ))
            # 组合回测扩展列（add-t-combined-backtest）
            conn.execute(text(
                "ALTER TABLE t_backtest_tasks ADD COLUMN IF NOT EXISTS symbols_json JSONB NOT NULL DEFAULT '[]'"
            ))
            conn.execute(text(
                "ALTER TABLE t_backtest_tasks ADD COLUMN IF NOT EXISTS build_mode BOOLEAN NOT NULL DEFAULT FALSE"
            ))
            conn.execute(text(
                "ALTER TABLE t_backtest_tasks ADD COLUMN IF NOT EXISTS build_limit_ratio DOUBLE PRECISION NOT NULL DEFAULT 0.55"
            ))
            conn.execute(text(
                "ALTER TABLE t_backtest_tasks ADD COLUMN IF NOT EXISTS select_source VARCHAR(8) NOT NULL DEFAULT 'manual'"
            ))
            conn.execute(text(
                "ALTER TABLE t_backtest_tasks ADD COLUMN IF NOT EXISTS select_limit INTEGER NOT NULL DEFAULT 10"
            ))
            conn.execute(text(
                "ALTER TABLE t_backtest_tasks ADD COLUMN IF NOT EXISTS rolling_build BOOLEAN NOT NULL DEFAULT FALSE"
            ))
            conn.execute(text(
                "ALTER TABLE t_backtest_tasks ADD COLUMN IF NOT EXISTS rolling_scan BOOLEAN NOT NULL DEFAULT FALSE"
            ))
            # 震荡市模式（仅回测生效：放宽趋势闸门 + 门槛）
            conn.execute(text(
                "ALTER TABLE t_backtest_tasks ADD COLUMN IF NOT EXISTS relax_mode BOOLEAN NOT NULL DEFAULT FALSE"
            ))
            # 板块轮动增强（add-sector-rotation）：任务级行业因子/过滤/轮动参数
            conn.execute(text(
                "ALTER TABLE t_backtest_tasks ADD COLUMN IF NOT EXISTS sector_params_json JSONB NOT NULL DEFAULT '{}'"
            ))
            # 2) 事件流（触发/复核/拦截/缺口）
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS t_backtest_events (
                    id BIGSERIAL PRIMARY KEY,
                    task_id BIGINT NOT NULL,
                    event_type VARCHAR(32) NOT NULL,
                    trade_day VARCHAR(10),
                    bar_time VARCHAR(24),
                    data_json JSONB,
                    created_at TIMESTAMP NOT NULL DEFAULT now()
                )
                """
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_t_backtest_events_task ON t_backtest_events (task_id, id)"
            ))
            # 3) 成交明细
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS t_backtest_trades (
                    id BIGSERIAL PRIMARY KEY,
                    task_id BIGINT NOT NULL,
                    symbol VARCHAR(16) NOT NULL,
                    side VARCHAR(8) NOT NULL,
                    price DOUBLE PRECISION,
                    volume INTEGER,
                    realized_pnl DOUBLE PRECISION DEFAULT 0,
                    fees DOUBLE PRECISION DEFAULT 0,
                    trigger_time VARCHAR(24),
                    exec_time VARCHAR(24),
                    created_at TIMESTAMP NOT NULL DEFAULT now()
                )
                """
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_t_backtest_trades_task ON t_backtest_trades (task_id, id)"
            ))
            # 4) 权益曲线
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS t_backtest_equity_snapshots (
                    id BIGSERIAL PRIMARY KEY,
                    task_id BIGINT NOT NULL,
                    trade_date VARCHAR(10) NOT NULL,
                    total_asset DOUBLE PRECISION,
                    realized_pnl DOUBLE PRECISION DEFAULT 0,
                    position INTEGER,
                    close DOUBLE PRECISION,
                    created_at TIMESTAMP NOT NULL DEFAULT now()
                )
                """
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_t_backtest_equity_task ON t_backtest_equity_snapshots (task_id, trade_date)"
            ))
            # 5) 指标报告（JSONB）
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS t_backtest_metrics (
                    id BIGSERIAL PRIMARY KEY,
                    task_id BIGINT NOT NULL,
                    metrics_json JSONB NOT NULL DEFAULT '{}',
                    caliber_notes JSONB NOT NULL DEFAULT '[]',
                    created_at TIMESTAMP NOT NULL DEFAULT now()
                )
                """
            ))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_t_backtest_metrics_task ON t_backtest_metrics (task_id)"
            ))

        print("[DB] PATCH: 做T回测迁移完成 (t_backtest_tasks/events/trades/equity_snapshots/metrics)")
    except Exception as e:
        print(f"[DB] PATCH warn (t backtest tables): {e}")


def _apply_ai_led_migration():
    """AI 主导做T迁移（幂等）：t_ai_actions 决策审计表 + t_conditions 发布者/会话列。"""
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            # 1) t_ai_actions — AI 决策审计（每步决策可追溯）
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS t_ai_actions (
                    id BIGSERIAL PRIMARY KEY,
                    session_id VARCHAR(64),
                    trade_date VARCHAR(10) NOT NULL,
                    symbol VARCHAR(16) NOT NULL,
                    action_type VARCHAR(24) NOT NULL,
                    input_snapshot JSONB NOT NULL DEFAULT '{}',
                    output JSONB NOT NULL DEFAULT '{}',
                    gateway_result JSONB,
                    created_at TIMESTAMP NOT NULL DEFAULT now()
                )
                """
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_t_ai_actions_date_symbol ON t_ai_actions (trade_date, symbol)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_t_ai_actions_session ON t_ai_actions (session_id, created_at)"
            ))
            # 4) t_ai_actions.outcome — 决策结果回填（成交价/后续走向/实际盈亏，成交后写入）
            conn.execute(text(
                "ALTER TABLE t_ai_actions ADD COLUMN IF NOT EXISTS outcome JSONB"
            ))
            # 2) t_conditions — 发布者与会话（条件即定时器归属）
            conn.execute(text(
                "ALTER TABLE t_conditions ADD COLUMN IF NOT EXISTS publisher VARCHAR(16) NOT NULL DEFAULT 'rule'"
            ))
            conn.execute(text(
                "ALTER TABLE t_conditions ADD COLUMN IF NOT EXISTS session_id VARCHAR(64)"
            ))
            # 3) t_build_events.status — pending_confirmation(20字符) 超 VARCHAR(16)，加长
            conn.execute(text(
                "ALTER TABLE t_build_events ALTER COLUMN status TYPE VARCHAR(32)"
            ))

        print("[DB] PATCH: AI 主导做T迁移完成 (t_ai_actions + t_conditions publisher/session_id)")
    except Exception as e:
        print(f"[DB] PATCH warn (ai_led tables): {e}")
