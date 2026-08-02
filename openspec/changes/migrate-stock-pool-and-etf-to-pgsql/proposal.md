## Why

平台的核心主数据（全 A 股股票池、行业/概念板块映射、ETF 池）仍分散在本地 SQLite 文件（`data/stock_pool.db`、`data/cache.db`）中，8+ 个 API 与服务各自重复 `sqlite3.connect` 打开同一文件，无法与已迁入 PostgreSQL 的 paper engine、backtest、golden pit 等数据 JOIN，也难以利用 PG 的并发读写与远程部署能力。`data/trades.db` 中仅剩 `sector_config` 仍被盘前扫描活跃读写，`watchlist` 已无任何代码引用。本次将股票池、ETF 池与板块配置迁入 PostgreSQL，删除遗留 watchlist，并让通用查询接口改走 PG；news.db 数据不迁移，继续保留在 SQLite。

## What Changes

- **迁移 `stock_pool.db` 到 PostgreSQL**：在 SQLAlchemy 中新增 5 张表模型（`stock_pool`、`sectors`、`concept_sectors`、`stock_concept_map`、`stock_sector_map`），一次性数据迁移脚本，并将所有读写方从 `sqlite3.connect` 切换为 PG 查询。
- **迁移 ETF 池到 PostgreSQL**：新增 `etf_pool` 表模型，`core/xueqiu_engine.py` 的写入（`sync_etf_pool`）与读取（`get_etf_pool_from_db`）改为读写 PG，数据从 `data/cache.db` 迁移。
- **迁移 `sector_config` 到 PostgreSQL**：新增 `sector_config` 表模型（`data/trades.db` 中最后一张活表），`jobs/pre_market_scan.py` 的 `SectorConfigManager` 读写改 PG，`seed_golden_pit_etf_config.py` 生成的同步 SQL 适配 PG。
- **删除 `watchlist` 表**：`data/trades.db` 的 `watchlist` 表（132 行）已无任何代码读写，直接 DROP，不迁移数据。
- **BREAKING**: `/api/v1/db` 通用查询接口从"直接打开本地 SQLite 文件"改为"查询 PostgreSQL"（`backend/app/api/db.py`），`db` 参数映射为 PG 表。
- **news.db 数据不迁移**：保持 SQLite 存储；代码仅做必要适配（通用查询接口对 `news` 保留 SQLite 只读通道）。
- 迁移完成后，`data/stock_pool.db`、`data/cache.db` 的 `etf_pool`、`data/trades.db` 的 `sector_config` 不再作为运行时数据源（文件保留作备份）。

## Capabilities

### New Capabilities
- `market-reference-data`: 股票池/行业/概念/ETF 池主数据持久化于 PostgreSQL，供后端 API 与 jobs 统一读写。
- `sector-config-pgsql`: 板块联动配置（`sector_config`）持久化于 PostgreSQL，盘前扫描与 seed 脚本读写 PG；遗留 `watchlist` 表被删除。
- `db-query-api`: 通用数据库查询 API 基于 PostgreSQL 查询，仅 `news` 保留 SQLite 只读通道。

### Modified Capabilities

<!-- 无：market 等现有 spec 描述的是 API 返回内容，数据源属于实现细节，需求本身不变。 -->

## Impact

- **数据层**：`backend/app/models/market_orm.py` 新增 7 个 SQLAlchemy 模型（stock_pool 5 表 + `etf_pool` + `sector_config`）；`backend/app/database.py` 注册模型。
- **迁移/清理脚本**：新增 `scripts/migrate_market_data_to_pgsql.py`（照 `scripts/migrate_sqlite_to_pgsql.py` 模式，支持 `--dry-run`），覆盖 stock_pool 5 表、`etf_pool`、`sector_config`；迁移时 DROP `watchlist`。
- **后端 API（读方）**：`backend/app/api/market.py`、`trades.py`、`portfolio.py`、`indicator.py`、`news.py`、`etf.py`、`db.py`。
- **后端服务（读方）**：`backend/app/services/industry_leaderboard.py`、`local_data_provider.py`。
- **Jobs（读/写方）**：`jobs/pre_market_scan.py`（`SectorConfigManager` 读写 `sector_config`）、`jobs/stock_selector.py`、`jobs/fund_flow.py`、`jobs/market_scan.py`。
- **写入方**：`core/stock_pool_manager.py`（股票池刷新）、`core/xueqiu_engine.py`（ETF 池同步）。
- **其他读方**：`apps/trader/etf_selector.py`（`stock_pool.industry` 成分查询）。
- **配套脚本**：`scripts/seed_golden_pit_etf_config.py`、`backend/scripts/seed_golden_pit_etf_config.py` 的 `sector_config` 同步 SQL 适配 PG。
- **依赖**：`psycopg2`（core/jobs 侧，与 `paper_engine.py` 一致）、SQLAlchemy（后端侧）；`DATABASE_URL` 环境变量为连接来源。