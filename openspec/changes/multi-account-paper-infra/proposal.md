## Why

当前模拟盘只有单一账户（`paper_account_info` 单行、持仓以 symbol 为主键、无账户维度），所有交易模块（股票 Pi 自主交易、候选池/止损/加仓监控）共用同一份现金、持仓与风控状态。黄金坑 DCA 需要接入模拟盘执行，但股票任务正在运行，两者共用账户会导致回撤熔断、连亏熔断、资金上限互相干扰；后续做 T agent 也需要独立账户。需要一个通用的多账户隔离底座。

## What Changes

- 新增账户注册表 `paper_accounts`（account_id、模块归属、初始资金、启用状态），作为多账户入口。
- 全部 paper 表（`paper_account_info` / `paper_positions` / `paper_orders` / `paper_trades` / `paper_daily_snapshot` / `paper_capital_adjustments`）增加 `account_id` 维度；positions 主键改为 `(account_id, symbol)`，快照主键改为 `(account_id, trade_date)`。
- 默认账户 `stock`（存量数据零迁移，行为不变），黄金坑账户 `golden_pit`（初始资金 200,000）。
- `PaperTradingEngine` / `MarcusVNPyExecutor` 支持 `account_id` 参数，所有 SQL 与风控（现金、回撤、连亏、单笔仓位、T+1）按账户隔离。
- vnpy bridge 保持股票账户单例不变；非股票账户走 `PaperTradingEngine` 直连 PostgreSQL。
- `/api/v1/trades` 增加 `account` 字段（默认 `stock`）；`/api/v1/portfolio` 增加 `account` 参数，FIFO 重放、快照、资金调整按账户过滤。
- Portfolio 页面增加账户切换器，支持查看/合并多账户视图。
- **BREAKING**：`paper_positions` 主键从 `symbol` 变为 `(account_id, symbol)`，涉及持仓查询 SQL 需按账户过滤。

## Capabilities

### New Capabilities
- `paper-accounts`: 模拟盘多账户注册与隔离——账户注册表、账本按账户维度隔离、执行器按账户分派、风控状态按账户独立。

### Modified Capabilities
- `trading`: 交易执行与查询支持 `account` 维度（下单选账户、历史按账户过滤）。
- `portfolio`: 组合视图按账户隔离并支持账户切换。

## Impact

- 数据库：6 张 paper 表结构变更 + 新增 `paper_accounts` 表；存量数据默认归入 `stock` 账户。
- 后端：`backend/app/models/paper_trade.py`、`backend/app/api/trades.py`、`backend/app/api/portfolio.py`、`backend/app/core/trading/marcus_trade.py`、`backend/app/core/trading/vnpy_bridge.py`、`backend/app/core/trading/vnpy_listeners.py`、`backend/app/services/trade_graph.py`、`backend/app/services/stop_loss_monitor.py`。
- 引擎：`apps/paper-trading/paper_engine.py`（账户参数 + SQL 过滤 + 订单计数按账户）。
- 前端：`frontend/src/pages/PortfolioPage.tsx` 及 portfolio API 客户端。
- 调度：`backend/app/services/scheduler_service.py`、`backend/app/api/scheduler.py` 的监控任务绑定账户。
