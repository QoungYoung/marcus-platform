## Context

父 change `migrate-stock-pool-and-etf-to-pgsql` 已把股票池主数据（`stock_pool`、`sectors`、`concept_sectors`、`stock_concept_map`、`stock_sector_map`）与 ETF 池迁入 PostgreSQL，并将写方（`StockPoolManager`、`XueqiuEngine`）及后端 API/jobs 读方切到 PG。但当时未覆盖 3 个直读 `data/stock_pool.db` 的读方，迁移后它们会读到不再刷新的陈旧数据。本 change 将剩余读方统一切到 PG。

## Goals / Non-Goals

**Goals:**
- 消除 `stock_pool.db` 的全部运行时读方，使该文件仅作为备份存在。
- 保持每个读方现有的降级行为（Xueqiu 兜底 / 返回原始值 / 空列表）不变。

**Non-Goals:**
- 不迁移 `news.db`、`trades.db`、`cache.db` 其余表（仍留在 SQLite）。
- 不引入新的数据模型或 schema 变更。
- 不重构新闻管线的业务逻辑。

## Decisions

### D1: 统一 psycopg2 + DATABASE_URL 直连模式
与 `core/stock_pool_manager.py` / jobs 侧一致，在每个模块内添加轻量 `_pg_conn()` 辅助函数（`import psycopg2`；`DATABASE_URL` 缺省回退 `postgresql://marcus:marcus123@localhost:5432/marcus_trading`），替换对应 `sqlite3.connect` 块。
- **理由**：这些模块处于 core/apps/scripts 层，不依赖 backend 的 `SessionLocal`；保持与既有 jobs 模式一致，改动最小。

### D2: core/news_analyzer.py 概念兜底 → PG
将 `normalize_concept()` 词汇表未命中时的回退块从：
`sqlite3.connect(data/stock_pool.db)` + `SELECT DISTINCT concept_name FROM stock_concept_map`
改为 `_pg_conn()` + 同 SQL（`%s` 无参数）。查询失败仍 `pass` 并返回原始概念名。

### D3: news_catalyst_tracker.py Step 1 → PG
`batch_update_catalysts()` Step 1 从：
`sqlite3.connect(STOCK_POOL_DB)` + `SELECT symbol, name FROM stock_pool WHERE symbol IN (?,...)`
改为 `_pg_conn()` + `SELECT symbol, name FROM stock_pool WHERE symbol IN (%s,...)`（按 codes 动态生成占位符）。移除 `STOCK_POOL_DB` 常量；Step 2 Xueqiu 补漏逻辑不变。

### D4: dump_direction_data.py → PG
主流程股票池块从 SQLite 连接改为 `_pg_conn()`，SQL 保持不变（`WHERE is_st = 0 AND industry IS NOT NULL AND industry != ''`），`all_symbols`/`symbol_info` 后续逻辑不变。

### D5: sync_industry_stocks.py 清理
移除 `STOCK_POOL_DB = WORKSPACE / "data" / "stock_pool.db"`（无任何引用），不改变该脚本 Tushare 数据来源。

## Risks / Trade-offs

- [远端 PG 延迟] → 名称批量查询按代码列表一次 `IN` 查询（非 N+1）；`news_catalyst_tracker` 与 `news_analyzer` 的查询频率低（批次/兜底场景），可接受单次连接开销。
- [PG 不可用时的降级] → 所有替换点保留 `try/except`：概念兜底返回原始名、名称查询走 Xueqiu、dump 报错退出，与原 SQLite 缺失时的行为一致。
- [与父 change 的耦合] → 本 change 依赖父 change 已执行的 PG 数据迁移；若父 change 未部署，读方会读到空数据（有降级，不崩溃）。

## Migration Plan

1. 父 change `migrate-stock-pool-and-etf-to-pgsql` 完成数据迁移并部署后，合入本 change。
2. 验证：`py_compile` 全部改动文件；`rg "stock_pool.db"` 确认三模块无残留 SQLite 读方。
3. 运行一次 `batch_update_catalysts`（或触发一次新闻管线）与一次 `dump_direction_data.py --days 1`，确认读 PG 正常。
4. 回滚：三处读方改回 SQLite 版本即可（`stock_pool.db` 保留作备份）。

## Open Questions

- 无。
