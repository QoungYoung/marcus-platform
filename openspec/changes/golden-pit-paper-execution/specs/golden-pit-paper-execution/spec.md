## ADDED Requirements

### Requirement: DCA 买入腿在 golden_pit 账户落盘
黄金坑 DCA 主流程 SHALL 将每个买入腿（宽基/板块/载体 ETF）通过 `MarcusVNPyExecutor(account_id="golden_pit")` 下限价买单（现价 × 1.02），并将订单结果写入 `golden_pit_dca_log`。

#### Scenario: 买入腿成功落单
- **WHEN** 某买入腿金额 > 0 且下单成功
- **THEN** 系统 SHALL 记录 `status="filled"` 与返回的 `order_id`
- **THEN** 该 ETF 的持仓 SHALL 出现在 `golden_pit` 账户持仓中

#### Scenario: 买入腿下单失败
- **WHEN** 某买入腿下单失败（资金不足、风控拒绝、引擎异常）
- **THEN** 系统 SHALL 记录 `status="failed"` 并保留失败原因
- **THEN** 系统 SHALL 仍推送该买入通知，标注"未成交"

#### Scenario: 买入腿金额不足一手
- **WHEN** 买入腿金额换算后不足 100 股
- **THEN** 系统 SHALL 跳过该腿并记录 `status="failed"`，原因含"金额不足"

### Requirement: DCA 退出信号在 golden_pit 账户落盘
所有退出信号（宽基退出、防御承接、板块二次拐点、防御撤场）SHALL 通过 `MarcusVNPyExecutor(account_id="golden_pit")` 下限价卖单（现价 × 0.98），股数按当前持仓换算为 100 股整数倍。

#### Scenario: 退出卖单成功
- **WHEN** 退出信号触发且持仓足够
- **THEN** 系统 SHALL 记录 `status="filled"`、`order_id` 与 exit 策略
- **THEN** 卖出的持仓 SHALL 从 `golden_pit` 账户减少

#### Scenario: 退出卖单失败降级为通知
- **WHEN** 退出卖单失败（无持仓、引擎异常）
- **THEN** 系统 SHALL 记录 `status="failed"`（或降级 `notified`）并保留通知
- **THEN** 系统 SHALL 不重复卖出同一持仓

### Requirement: golden_pit 账户持仓作为唯一持仓来源
DCA 模块的持仓判断 SHALL 以 `golden_pit` 账户持仓为准，禁止使用 `stock` 账户或其他账户的持仓。

#### Scenario: 持仓判断按账户隔离
- **WHEN** `stock` 账户持有某 ETF 而 `golden_pit` 账户未持有
- **THEN** DCA 模块 SHALL 判定该 ETF 在 golden_pit 账户无持仓，可正常买入

#### Scenario: 已持仓跳过
- **WHEN** `golden_pit` 账户已持有某 ETF 且配置 `skip_if_already_holding=true`
- **THEN** 系统 SHALL 跳过该 ETF 的重复买入
