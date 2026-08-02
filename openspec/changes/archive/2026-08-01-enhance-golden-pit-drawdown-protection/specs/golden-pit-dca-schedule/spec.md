# golden-pit-dca-schedule Delta Specification

## Purpose

新增深度入坑（连续入坑 ≥30 天）的系统告警机制。

## ADDED Requirements

### Requirement: 深度入坑告警

系统 SHALL 在每日 DCA 定投报告生成时，检查每个交易中指数的 `days_in_pit`（连续处于黄金坑的天数）。当任一指数的 `days_in_pit >= 30` 时，系统 SHALL 在报告的"操作建议"段中附加深度入坑告警。

#### Scenario: 指数入坑超过 30 天触发告警

- **WHEN** 纳斯达克的 `days_in_pit` 为 31
- **THEN** 定投报告的操作建议段 SHALL 包含 `⚠️ 深度入坑告警` 行
- **THEN** 告警 SHALL 包含指数名称、入坑天数、当前贪婪值
- **THEN** 告警 SHALL 建议人工复核该指数的参数是否需要调整

#### Scenario: 指数入坑不足 30 天不触发告警

- **WHEN** 中证1000 的 `days_in_pit` 为 14
- **THEN** 深度入坑告警 SHALL NOT 出现
- **THEN** 报告正常生成，不附加额外内容

#### Scenario: 多个指数同时深度入坑

- **WHEN** 纳斯达克 `days_in_pit=35`，科创50 `days_in_pit=32`
- **THEN** 告警 SHALL 列出所有符合条件的指数
- **THEN** 每个指数一行，包含各自的天数和贪婪值

#### Scenario: 入坑天数在报告中始终可见

- **WHEN** 生成定投报告
- **THEN** 无论是否触发告警，已处于黄金坑的指数的 `days_in_pit` SHALL 在报告表格中展示
- **THEN** 深度入坑告警 SHALL 是对现有展示的补充，而非替代
