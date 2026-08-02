## 1. 读方切换 PostgreSQL

- [ ] 1.1 `core/news_analyzer.py` 概念标准化兕底查询改走 PG `stock_concept_map`（`SELECT DISTINCT concept_name`），保留失败返回原始名的降级
- [ ] 1.2 `apps/news/news_catalyst_tracker.py` Step 1 股票名称批量查询改走 PG `stock_pool`（`IN (%s,...)`），移除 `STOCK_POOL_DB` 常量，Xueqiu 补漏不变
- [ ] 1.3 `scripts/dump_direction_data.py` 股票池构建改走 PG `stock_pool`（`is_st = 0 AND industry 非空`）
- [ ] 1.4 `apps/news/sync_industry_stocks.py` 移除已无引用的 `STOCK_POOL_DB` 常量

## 2. 验证

- [ ] 2.1 `rg "stock_pool.db"` 确认三模块无残留 SQLite 读方
- [ ] 2.2 全部改动文件 `py_compile` 通过
