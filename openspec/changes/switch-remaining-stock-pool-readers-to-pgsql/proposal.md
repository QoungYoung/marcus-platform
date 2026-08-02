## Why

`migrate-stock-pool-and-etf-to-pgsql` 迁移完成后，`data/stock_pool.db` 已不再是运行时数据源（`StockPoolManager` 等写方均已改走 PostgreSQL），但仍有 3 个读方直接打开该 SQLite 文件读取股票池/概念映射：`core/news_analyzer.py`（概念名标准化兜底）、`apps/news/news_catalyst_tracker.py`（催化剂追踪的股票名称批量查询）、`scripts/dump_direction_data.py`（方向特征数据 dump 的全量股票池）。它们将继续读到陈旧数据，需要统一切到 PostgreSQL。

## What Changes

- `core/news_analyzer.py`：概念标准化兜底查询从 SQLite `stock_concept_map` 改为 PostgreSQL `stock_concept_map`（`SELECT DISTINCT concept_name`）。
- `apps/news/news_catalyst_tracker.py`：`batch_update_catalysts()` Step 1 的股票名称批量查询从 SQLite `stock_pool` 改为 PostgreSQL `stock_pool`；Xueqiu 补漏兜底保持不变。
- `scripts/dump_direction_data.py`：构建股票池的查询从 SQLite `stock_pool` 改为 PostgreSQL `stock_pool`。
- `apps/news/sync_industry_stocks.py`：移除已无代码引用的 `STOCK_POOL_DB` 常量（该脚本实际通过 Tushare 生成行业关键词文件）。
- 统一使用 `psycopg2` + `DATABASE_URL` 连接（与 `core/stock_pool_manager.py`、jobs 侧模式一致），各替换点保留原有降级逻辑（Xueqiu 兜底 / 返回原始值 / 返回空）。

## Capabilities

### New Capabilities
- `stock-pool-readers-pgsql`: 新闻与数据管道中的剩余股票池/概念映射读方统一从 PostgreSQL 读取主数据，不再直读 `data/stock_pool.db`。

### Modified Capabilities

<!-- 无：主 specs 中无相关需求发生变化；本 change 是对 `market-reference-data` 增量 spec 的收尾补充。 -->

## Impact

- **读方代码**：`core/news_analyzer.py`、`apps/news/news_catalyst_tracker.py`、`scripts/dump_direction_data.py`、`apps/news/sync_industry_stocks.py`。
- **依赖**：`psycopg2`（core/jobs 侧已使用）、`DATABASE_URL` 环境变量。
- **数据层**：无 schema 变更、无数据迁移（数据已由父 change `migrate-stock-pool-and-etf-to-pgsql` 写入 PG）。
- **回滚**：三个读方改回 SQLite 版本即可；`data/stock_pool.db` 仍保留作备份。
