## ADDED Requirements

### Requirement: 账户注册表
系统 SHALL 提供账户注册表 `paper_accounts`，记录每个模拟盘账户的 `account_id`、名称、所属模块、初始资金与启用状态，作为所有交易模块获取账户的唯一入口。

#### Scenario: 注册默认账户
- **WHEN** 系统初始化模拟盘数据
- **THEN** 注册表 SHALL 包含 `stock` 账户（股票任务，初始资金为现有账户资金）
- **THEN** 注册表 SHALL 包含 `golden_pit` 账户（黄金坑 DCA，初始资金 250,000）

#### Scenario: 查询全部账户
- **WHEN** 调用账户列表接口
- **THEN** 返回所有启用的账户，包含 account_id、名称、模块、初始资金与可用资金

#### Scenario: 未知账户拒绝执行
- **WHEN** 使用注册表中不存在的 account_id 发起交易
- **THEN** 系统 SHALL 拒绝该请求并返回错误

### Requirement: 账本按账户隔离
系统 SHALL 在全部模拟盘账本表（`paper_account_info` / `paper_positions` / `paper_orders` / `paper_trades` / `paper_daily_snapshot` / `paper_capital_adjustments`）中以 `account_id` 维度隔离数据：每个账户拥有独立的现金、持仓、订单、成交、每日快照与资金调整。

#### Scenario: 持仓按账户隔离
- **WHEN** `stock` 账户买入 SH600519 且 `golden_pit` 账户买入 SH512480
- **THEN** `paper_positions` SHALL 分别记录 `(stock, SH600519)` 与 `(golden_pit, SH512480)` 两行
- **THEN** 查询任一账户持仓 SHALL NOT 返回另一账户的持仓

#### Scenario: 现金按账户隔离
- **WHEN** `golden_pit` 账户买入消耗资金
- **THEN** `stock` 账户的可用资金 SHALL 保持不变

#### Scenario: 订单序号按账户独立
- **WHEN** 两个账户各自下单
- **THEN** 每个账户的订单序号 SHALL 独立递增，订单号不冲突

### Requirement: 执行器按账户分派
系统 SHALL 允许 `MarcusVNPyExecutor` 指定 `account_id`，其所有交易、查询与风控逻辑仅作用于该账户。

#### Scenario: 黄金坑使用独立执行器
- **WHEN** 黄金坑 DCA 创建执行器并指定 `account_id="golden_pit"`
- **THEN** 该执行器的买入/卖出 SHALL 仅写入 `golden_pit` 账户账本
- **THEN** 该执行器的回撤熔断、连亏熔断、单笔仓位限制 SHALL 基于 `golden_pit` 账户自身状态计算

#### Scenario: 股票任务保持原账户
- **WHEN** 股票任务不指定账户
- **THEN** 执行器 SHALL 默认使用 `stock` 账户，行为与改造前一致

### Requirement: 账户风控隔离
系统 SHALL 保证不同账户之间的风控状态互不影响：一个账户的回撤熔断或连续亏损计数 SHALL NOT 阻止另一个账户的买入。

#### Scenario: 股票回撤不阻塞黄金坑
- **WHEN** `stock` 账户总回撤达到 -6% 触发熔断
- **THEN** `golden_pit` 账户的买入 SHALL 仍可正常执行（基于其自身回撤判断）
