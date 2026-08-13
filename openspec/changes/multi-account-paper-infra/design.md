## Context

模拟盘目前是单账户架构：`paper_account_info` 只有一行（硬编码 `WHERE id=1`），`paper_positions` 以 symbol 为主键，`paper_trades` / `paper_orders` / `paper_daily_snapshot` / `paper_capital_adjustments` 均无账户维度。股票任务（Pi 自主交易 + 候选池/长期池/加仓/止损监控）全部通过 `MarcusVNPyExecutor` 写入同一账户；黄金坑 DCA 需要接入模拟盘执行，后续做 T agent 也需要独立账户。改造必须对正在运行的股票任务零影响。

## Goals / Non-Goals

**Goals:**
- 建立通用多账户底座：账户注册表 + 账本按 `account_id` 隔离。
- 存量 `stock` 账户行为完全不变（默认账户、零数据迁移）。
- 黄金坑获得独立账户（初始资金 250,000），风控（回撤/连亏/仓位）按账户独立。
- 交易/组合 API 与 Portfolio 页面支持账户维度与切换。
- 为做 T agent 预留"注册新账户即可用"的扩展点。

**Non-Goals:**
- 不做实盘券商接入。
- 不做 vnpy bridge 多账户化（黄金坑/做 T 不走 vnpy，走 PaperTradingEngine）。
- 不做做 T agent 的具体策略与监控任务（属于后续独立 change）。
- 不重写 portfolio FIFO 算法，只加账户过滤。

## Decisions

### D1: 账户标识用字符串 `account_id`，注册表 `paper_accounts`
用可读字符串（`stock` / `golden_pit` / `t_agent`）而非自增整数，便于 agent 代码直接引用、日志可读、前端展示。
- 备选：整数 id 关联注册表 → 代码中到处查表，可读性差，弃用。
- `paper_accounts` 字段：`account_id (PK)`, `name`, `module`, `initial_capital`, `enabled`, `created_at`。种子数据：`stock`（现有资金）、`golden_pit`（250,000）。

### D2: 6 张 paper 表加 `account_id` 列，不做分库分表
`paper_positions` 主键 → `(account_id, symbol)`；`paper_daily_snapshot` 主键 → `(account_id, trade_date)`；其余表加 `account_id` 索引列（默认 `'stock'`）。
- 备选：按账户拆表（`paper_positions_golden_pit` 等）→ 代码重复、迁移痛苦、无法统一查询，弃用。
- 全部历史 SQL 不指定 account 即默认 `stock`，存量数据零迁移成本。

### D3: 执行器/引擎增加 `account_id` 参数，风控状态按账户实例隔离
- `PaperTradingEngine(account_id='stock', initial_capital=...)`：`_init_account` / `_save_account` / 持仓 / 订单 / 成交 / `match_order` / `cancel_order` 全部按 account 过滤；订单计数按账户独立（orderid 前缀区分：`ORD` / `GP` / `T0`）。
- `MarcusVNPyExecutor(account_id=..., bridge=..., engine=...)`：`check_risk` 的现金、回撤（读该账户总盈亏）、单笔 40% 仓位（该账户 initial_capital）按账户；连亏计数是执行器实例状态，天然按账户隔离。
- 改造 `__init__` 支持显式传入 engine（现在 bridge 非空就忽略 engine），并让 `account_id` 默认 `'stock'`。

### D4: vnpy bridge 保持 `stock` 单例，其他账户走 PaperTradingEngine
vnpy `PaperAccountApp` 内部是单账户模型，多实例需多 Qt 事件循环，脆弱且收益低。
- `vnpy_bridge.py` / `vnpy_listeners.py` 所有 SQL 显式绑定 `account_id='stock'`。
- 黄金坑/做 T 用 `PaperTradingEngine(account_id=...)` 直连 PostgreSQL。
- 行锁（`SELECT ... FOR UPDATE`）按账户行加锁，跨账户并发互不阻塞，同一账户内保持原有并发安全语义。

### D5: API 与前端账户维度
- `GET /api/v1/accounts`：列出启用账户（新增轻量 endpoint，可用 FastAPI router 或并入 portfolio）。
- `POST /api/v1/trades`：请求体加 `account`（默认 `stock`），未知账户返回 400。
- `GET /api/v1/portfolio*`：全部加 `account` query 参数（默认 `stock`），FIFO 重放、现金、快照、资金调整按账户过滤。
- Portfolio 页：顶部账户切换器（数据来自 `/api/v1/accounts`），切换后重载 summary/positions/equity；接口层预留合并视图（本期可不做 UI 合并）。

### D6: 迁移在启动时幂等执行
沿用仓库现有"启动时 CREATE TABLE IF NOT EXISTS"模式，迁移步骤：
1. `ALTER TABLE ... ADD COLUMN IF NOT EXISTS account_id VARCHAR(16) NOT NULL DEFAULT 'stock'`（6 张表）。
2. 重建 `paper_positions` / `paper_daily_snapshot` 主键为复合主键（若原主键存在则 drop 后重建）。
3. 建 `paper_accounts` 并 upsert 种子行（`stock`、`golden_pit`）。
4. 新账户的 `paper_account_info` 行由引擎首次使用时自动创建（初始资金 250,000）。
回滚：删除 `golden_pit` 注册行 + 相关账本行即可退回单账户行为，`stock` 不受影响。

## Risks / Trade-offs

- [vnpy 监听器仍写硬编码 `id=1`] → 迁移时统一改为 `account_id='stock'`，并加单测断言股票任务写 stock 账户。
- [Portfolio FIFO 重放若忘记按账户过滤会混算] → 所有查询统一走 engine/ORM 的 account 过滤封装，禁止裸 SQL 按 symbol 查；code review 清单检查。
- [复合主键重建在 PG 上可能锁表] → 数据量小（单用户模拟盘），启动时迁移可接受；若担心可放独立迁移脚本，但仓库现状是启动迁移。
- [黄金坑与股票共用同一 orderid 前缀风险] → orderid 增加账户前缀（`GP`/`T0`），计数按账户独立。
- [未来做 T 需要盘中高频] → 本期只保证账户隔离与执行器分派，性能与 T+1 底仓逻辑在 t_agent 专属 change 处理；现有 T+1 规则（可用 = 持仓 - 当日买入）已天然支持底仓回转。
