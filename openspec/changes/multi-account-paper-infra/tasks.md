## 1. 数据层迁移

- [x] 1.1 新增 `paper_accounts` 注册表模型（account_id、name、module、initial_capital、enabled、created_at）并接入 `backend/app/models/paper_trade.py`
- [x] 1.2 为 6 张 paper 表（paper_account_info/paper_positions/paper_orders/paper_trades/paper_daily_snapshot/paper_capital_adjustments）增加 `account_id` 字段，默认 `'stock'`
- [x] 1.3 将 `paper_positions` 主键改为 `(account_id, symbol)`、`paper_daily_snapshot` 主键改为 `(account_id, trade_date)`
- [x] 1.4 编写幂等启动迁移（ALTER TABLE ADD COLUMN IF NOT EXISTS + 复合主键重建 + 注册表种子 upsert：stock、golden_pit 初始资金 250000）
- [x] 1.5 为 paper_orders/paper_trades 增加 `account_id` 索引

## 2. 引擎层（PaperTradingEngine）

- [x] 2.1 `PaperTradingEngine` 增加 `account_id` 参数（默认 `'stock'`），`_init_account`/`_save_account` 按账户读写（替换 `WHERE id=1` 硬编码）
- [x] 2.2 持仓/订单/成交/快照查询与写入全部按 `account_id` 过滤
- [x] 2.3 订单序号按账户独立计数，orderid 增加账户前缀（stock=`ORD`、golden_pit=`GP`）
- [x] 2.4 `match_order`/`cancel_order` 的 `SELECT ... FOR UPDATE` 改为按账户行加锁
- [x] 2.5 引擎启动时自动为缺失的账户创建 `paper_account_info` 行（用注册表 initial_capital）

## 3. 执行器层（MarcusVNPyExecutor）

- [x] 3.1 `MarcusVNPyExecutor` 增加 `account_id` 参数并透传到 engine/check_risk
- [x] 3.2 支持显式传入 engine 实例（当前 bridge 非空时忽略 engine，需调整 `__init__`）
- [x] 3.3 `check_risk` 的现金、回撤熔断（读该账户总盈亏）、单笔 40% 仓位（该账户 initial_capital）改为按账户实例计算
- [x] 3.4 `get_account`/`get_positions_from_db`/`_get_today_buy_volumes`/成交查询全部按账户过滤

## 4. vnpy 绑定 stock 账户

- [x] 4.1 `vnpy_bridge.py` 中 `paper_account_info`/positions/trades 查询显式绑定 `account_id='stock'`
- [x] 4.2 `vnpy_listeners.py` 异步写入 orders/trades/account_info/positions 显式绑定 `account_id='stock'`

## 5. API 层

- [x] 5.1 新增 `GET /api/v1/accounts` 列出启用账户（含 initial_capital、available_cash）
- [x] 5.2 `POST /api/v1/trades` 请求体增加 `account` 字段（默认 `stock`），未知账户返回 400
- [x] 5.3 `/api/v1/trades` 历史/订单查询支持 `account` 过滤
- [x] 5.4 `/api/v1/portfolio` 系列接口（summary/positions/equity/快照/资金调整）增加 `account` 参数并按账户过滤 FIFO 重放
- [x] 5.5 portfolio 资金调整/作废交易按账户隔离

## 6. 前端 Portfolio 账户切换

- [x] 6.1 portfolio API 客户端支持 `account` 参数
- [x] 6.2 Portfolio 页顶部增加账户切换器（数据来自 /api/v1/accounts）
- [x] 6.3 切换账户后重载 summary/positions/equity，页面头部显示当前账户名

## 7. 调度与监控绑定账户

- [x] 7.1 `trade_graph.py`/`stop_loss_monitor.py`/`candidate_pool_monitor.py`/`long_term_pool_monitor.py`/`position_tier_monitor.py` 的现金与连亏查询按 `stock` 账户过滤（显式 account_id）
- [x] 7.2 `backend/app/main.py`/`scheduler_service.py` 启动路径中股票任务执行器显式绑定 `stock` 账户

## 8. 测试与验证

- [x] 8.1 编写迁移幂等性测试（重复启动不报错、存量数据归入 stock）
- [x] 8.2 编写多账户隔离测试：两账户同 symbol 持仓互不覆盖、现金独立、风控独立
- [x] 8.3 编写 trades/portfolio API 的 account 参数测试（默认 stock、golden_pit、未知账户 400）
- [x] 8.4 运行现有测试套件，确认 stock 账户行为无回归
