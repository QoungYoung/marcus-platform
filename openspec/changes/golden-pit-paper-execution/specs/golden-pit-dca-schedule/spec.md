## ADDED Requirements

### Requirement: 买入执行状态推进
DCA 买入腿的执行状态 SHALL 从"仅通知"推进为"真实落盘"，执行成功后不得仅记录 `notified`。

#### Scenario: 落单成功后状态为 filled
- **WHEN** 买入腿在 `golden_pit` 账户成功成交
- **THEN** `golden_pit_dca_log` 中该腿 SHALL 记录 `status="filled"` 与 `order_id`

#### Scenario: 已执行日去重逻辑兼容 filled
- **WHEN** 查询本窗口已执行的定投日
- **THEN** `status="filled"` 与 `status="notified"` 均 SHALL 视为已执行（防止重复买入）
