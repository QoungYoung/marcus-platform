## Context

当前 paper engine 使用 SQLite `trades.db` 存储 5 张表。每次创建 `PaperTradingEngine` 实例时，从 SQLite 加载状态到内存，修改后写回。由于 `MarcusVNPyExecutor` 不是单例（每次 API 请求都 `new` 一个），多个实例并发读写同一 SQLite 文件。SQLite WAL 模式允许并发读，但写操作仍是文件级串行锁——两个实例同时读入 `available_cash`，各自修改，后写回的覆盖前一个，导致漂移。

PostgreSQL 已在项目中用于 backtest、system_state、stop_loss_log 等表，有现成的 SQLAlchemy 连接池（`SessionLocal`）。迁移后可通过行锁保证 `account_info` 的读写原子性。

## Goals / Non-Goals

**Goals:**
- 将 `orders`、`trades`、`positions`、`account_info`、`daily_snapshot` 5 张表从 SQLite 迁移到 PostgreSQL
- `account_info` 的 `available_cash`/`frozen_cash` 更新使用 `SELECT ... FOR UPDATE` 保证并发安全
- 保持 `paper_engine.py` 的内存数据结构（`self.positions`、`self.available_cash` 等）不变，只改持久化层
- `marcus_trade.py` 的 `get_account()` 简化——不再需要 FIFO 重放，直接读 PostgreSQL 的 `account_info`
- 提供一键迁移脚本，将现有 SQLite 数据导入 PostgreSQL

**Non-Goals:**
- 不改变 `stock_pool.db`（只读查询，无并发写入问题）
- 不改动 backtest 系统的 PostgreSQL 表
- 不引入 asyncpg（保持同步 psycopg2，与现有代码一致）
- 不改造 `PaperTradingEngine` 为单例（那是另一个议题）

## Decisions

### Decision 1: 持久化层用 raw psycopg2 而非 SQLAlchemy ORM

**选择**：`paper_engine.py` 内部使用 `psycopg2` 直连（通过 `psycopg2.extensions.connection`），不经过 SQLAlchemy Session。

**理由**：
- `paper_engine.py` 是独立模块（在 `apps/paper-trading/` 下），目前不依赖 FastAPI/SQLAlchemy
- 它需要精确控制事务边界（buy 冻结资金 + match_order 解冻 + 更新持仓必须在同一事务内）
- SQLAlchemy ORM 的 Unit of Work 模式会打乱 paper engine 的显式事务顺序
- `marcus_trade.py` 和 `portfolio.py` 等 backend 代码可以继续用 SQLAlchemy ORM 读这些表

**备选方案**：全部改用 SQLAlchemy ORM —— 需要改造 paper_engine 的事务模型，风险大。已否决。

### Decision 2: 使用 `SELECT ... FOR UPDATE` 行锁，而非 advisory lock

**选择**：在更新 `account_info` 时使用 `SELECT ... FOR UPDATE` 锁定该行。

```
BEGIN;
SELECT available_cash, frozen_cash FROM account_info WHERE id = 1 FOR UPDATE;
-- 在内存中计算新值
UPDATE account_info SET available_cash = %s, frozen_cash = %s WHERE id = 1;
COMMIT;
```

**理由**：
- 行锁是 PostgreSQL 标准机制，不需要额外的锁命名约定
- `account_info` 只有一行（`id=1`），锁定这行不影响其他表的并发
- 事务提交时自动释放锁，无需手动管理
- 比 advisory lock 更简单——advisory lock 需要记住 lock ID 且容易忘记释放

**备选方案**：advisory lock (`pg_advisory_xact_lock(1)`) —— 需要确保所有写入路径都调用同一个 lock ID，容易遗漏。已否决。

### Decision 3: 迁移策略 —— 启动时自动检测 + 手动脚本兜底

**选择**：`paper_engine.py` 初始化时检测 PostgreSQL 表是否存在，不存在则自动建表。数据迁移通过独立脚本 `scripts/migrate_sqlite_to_pgsql.py` 手动执行。

**理由**：
- DDL 自动建表保证部署简单（与现有 `Base.metadata.create_all()` 模式一致）
- 数据迁移是破坏性操作（SQLite → PostgreSQL），应手动执行并在迁移前备份
- 旧 SQLite `trades.db` 保留不删，作为回滚备份

**备选方案**：启动时自动迁移数据 —— 复杂且危险（数据量大时启动卡住、迁移失败导致系统不可用）。已否决。

### Decision 4: 连接管理 —— paper_engine 使用独立 psycopg2 连接

**选择**：`paper_engine.py` 在 `__init__` 时创建一个 psycopg2 连接，整个引擎生命周期复用。连接参数从环境变量读取（复用 `DATABASE_URL`）。

**理由**：
- paper_engine 每次 `match_order` 需要在一个事务内完成多步操作（更新持仓 + 更新资金 + 记录成交），长连接比每次新建更可靠
- 不在 SQLAlchemy 连接池内，避免被 pool recycle 打断事务
- 连接异常时自动重连

**备选方案**：每次操作新建连接 —— 与当前 SQLite 模式一致，但 PostgreSQL 连接开销远大于 SQLite。已否决。

### Decision 5: paper_engine 不依赖于 FastAPI app context

**选择**：`paper_engine.py` 直接从环境变量/配置文件读取 PostgreSQL 连接参数，不通过 FastAPI 的 `get_db()` 依赖注入。

**理由**：
- `paper_engine.py` 位于 `apps/paper-trading/`，是被 `marcus_trade.py`（backend）和 CLI 脚本共同引用的底层模块
- 引入 FastAPI 依赖会破坏其独立性
- 连接参数从 `DATABASE_URL` 环境变量解析即可

## Risks / Trade-offs

- **[Risk] paper_engine 直连 psycopg2 绕过 SQLAlchemy，表结构变更需手动同步** → 在 `backend/app/models/` 中维护 ORM 模型作为 schema 的单一真相源，paper_engine 的 DDL 与 ORM 模型保持一致（通过 code review 保证）
- **[Risk] PostgreSQL 连接不可用时系统完全不可用** → 比 SQLite 多了一个外部依赖，但 PostgreSQL 已在 Docker Compose 中运行，且有健康检查
- **[Risk] 迁移脚本执行期间系统不能交易** → 迁移是离线操作，选择在非交易时段执行
- **[Trade-off] `marcus_trade.py` 的 `get_account()` 不再需要 FIFO 重放，但也失去了"自愈"能力** → 行锁解决了根因，不需要自愈；如果 PostgreSQL 数据也被破坏，从 SQLite 备份恢复

## Migration Plan

1. 非交易时段停止 backend 服务
2. 备份 `data/trades.db`
3. 运行 `python scripts/migrate_sqlite_to_pgsql.py` 导入数据到 PostgreSQL
4. 部署新代码（paper_engine 自动创建表 + 读取 PostgreSQL）
5. 启动 backend，验证 `/api/v1/portfolio` 数据正确
6. 保留 `data/trades.db` 作为回滚备份（不删除）

回滚：恢复旧代码 + 从备份的 `trades.db` 恢复。

## Open Questions

- 是否需要将 `trades.db` 的 WAL 文件和 SHM 文件也纳入备份？（体积很小，建议一起备份）
