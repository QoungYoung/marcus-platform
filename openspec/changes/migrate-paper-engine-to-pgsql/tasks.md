## 1. PostgreSQL ORM 模型

- [x] 1.1 在 `backend/app/models/` 下新建 `paper_trade.py`，定义 5 个 SQLAlchemy 模型：`PaperOrder`、`PaperTrade`、`PaperPosition`、`PaperAccountInfo`、`PaperDailySnapshot`
- [x] 1.2 在 `backend/app/database.py` 的 `Base` 中注册新模型（或通过 import 触发 `create_all`）
- [x] 1.3 在 `_apply_schema_patches()` 中为新表补充可能遗漏的列（如 `trades.reason`、`trades.trade_date` 等已存在的 SQLite 列）

## 2. Paper Engine 持久化层改造

- [x] 2.1 `paper_engine.py` 添加 `_get_pg_conn()` 方法：从 `DATABASE_URL` 环境变量解析连接参数，创建 psycopg2 连接，设置 `autocommit=False`（显式事务控制）
- [x] 2.2 `_init_database()` 改为执行 PostgreSQL `CREATE TABLE IF NOT EXISTS` DDL（5 张表），移除 SQLite DDL
- [x] 2.3 `_init_account()` 改为从 PostgreSQL `account_info` 表加载状态；`_save_account()` 改为在事务内 `SELECT ... FOR UPDATE` + `UPDATE`/`INSERT`
- [x] 2.4 `_load_positions_from_db()` 改为从 PostgreSQL `trades` 表做 FIFO 重放
- [x] 2.5 `buy()`、`sell()`、`cancel_order()` 中的 `_save_account()` 调用保持，但底层已走 PostgreSQL
- [x] 2.6 `match_order()` 改为在同一 PostgreSQL 事务内完成：锁定 `account_info` → 更新持仓 → 记录成交 → 更新资金 → 提交
- [x] 2.7 `get_orders()`、`get_trades()`、`get_profit_summary()` 等查询方法改为查询 PostgreSQL
- [x] 2.8 `get_account_info()` 的 `use_market_price` 分支改为从 PostgreSQL 读取 `account_info`
- [x] 2.9 `update_position_meta()`、`remove_position_meta()` 改为操作 PostgreSQL `positions` 表
- [x] 2.10 移除 `_get_conn()` 中的 SQLite 连接逻辑，移除 SQLite WAL/busy_timeout 设置

## 3. Backend 调用方适配

- [x] 3.1 `marcus_trade.py` `get_account()` 简化：`available_cash` 直接从 PostgreSQL `account_info` 表读取（不再需要 FIFO 重放），移除漂移检测纠正逻辑
- [x] 3.2 `portfolio.py` `calculate_positions_from_db()` 改为从 PostgreSQL 查询 trades、account_info
- [x] 3.3 `portfolio.py` `save_daily_snapshot()` 改为写入 PostgreSQL `daily_snapshot` 表
- [x] 3.4 `portfolio.py` `_calc_week_pnl()` 改为查询 PostgreSQL
- [x] 3.5 `trades.py` 交易撤回、历史查询等 SQLite 直连改为 PostgreSQL（通过 SQLAlchemy Session 或 ORM 模型）

## 4. 数据迁移

- [x] 4.1 新建 `scripts/migrate_sqlite_to_pgsql.py`：从 SQLite `trades.db` 读取全部数据，写入 PostgreSQL 对应表
- [x] 4.2 迁移脚本支持 `--dry-run` 参数：仅打印将要迁移的行数，不实际写入
- [x] 4.3 迁移脚本写入前清空目标表（`DELETE FROM` 或 `TRUNCATE`），保证幂等

## 5. 验证

- [ ] 5.1 在本地 PostgreSQL 环境执行迁移脚本，验证数据完整性
- [ ] 5.2 启动 backend，调用 `/api/v1/portfolio` 验证账户数据和持仓正确
- [ ] 5.3 并发测试：两个 `PaperTradingEngine` 实例同时 `buy()`，验证 `available_cash` 无漂移
- [ ] 5.4 验证旧 `trades.db` 文件未被删除（作为备份保留）
