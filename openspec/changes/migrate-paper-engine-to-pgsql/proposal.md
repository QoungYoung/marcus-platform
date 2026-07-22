## Why

SQLite 的 `account_info.available_cash` 因多实例并发写入而漂移（写-写冲突，后写覆盖先写），导致总资产少算约 3 万元。SQLite WAL 模式只提供读并发，写入仍是文件级串行锁——多个 `PaperTradingEngine` 实例各自读入内存、修改、写回，没有行级锁保证一致性。PostgreSQL 已有现成连接池和 ORM，迁移后可用 `SELECT ... FOR UPDATE` 或 advisory lock 保证 `account_info` 的写入原子性，从根上消除漂移。

## What Changes

- **BREAKING**: `paper_engine.py` 的 5 张 SQLite 表（`orders`、`trades`、`positions`、`account_info`、`daily_snapshot`）迁移到 PostgreSQL
- **BREAKING**: `trades.db` 文件不再作为实时交易数据源，仅保留为历史备份
- `account_info` 表的 `available_cash`/`frozen_cash` 写入加 `SELECT ... FOR UPDATE` 行锁，确保并发安全
- `marcus_trade.py` 的 FIFO 重放逻辑可简化——`account_info.available_cash` 不再需要漂移检测和纠正
- `portfolio.py` 的 `calculate_positions_from_db()` 数据源从 SQLite 切换到 PostgreSQL

## Capabilities

### New Capabilities
- `paper-engine-pgsql`: Paper trading engine 的数据持久化层从 SQLite 迁移到 PostgreSQL，包含 DDL、连接管理、行级锁保障的并发安全写入

### Modified Capabilities
- `trading`: 实时交易数据（订单、成交、持仓、账户状态）的存储介质从 SQLite 变更为 PostgreSQL，`account_info` 写入受行锁保护
- `portfolio`: 组合查询的数据源从 SQLite `trades.db` 切换到 PostgreSQL 对应表

## Impact

- `apps/paper-trading/paper_engine.py` — 全部 `_init_database()` DDL、`_get_conn()` 连接、`_save_account()`、`match_order()` 等 CRUD 改用 SQLAlchemy/psycopg2
- `backend/app/core/trading/marcus_trade.py` — `get_account()` 移除 FIFO 重放和漂移纠正（不再是必要的），直接读取 PostgreSQL `account_info`
- `backend/app/api/portfolio.py` — `calculate_positions_from_db()`、`save_daily_snapshot()`、`_calc_week_pnl()` 数据库连接切换
- `backend/app/api/trades.py` — 交易撤回等操作中的 SQLite 直连切换为 PostgreSQL
- `backend/app/models/` — 新增 5 个 SQLAlchemy ORM 模型（对应 SQLite 的 5 张表）
- `backend/app/database.py` — `_apply_schema_patches()` 可能需要新增迁移列
- 不影响前端、AI 决策链路、回测系统
