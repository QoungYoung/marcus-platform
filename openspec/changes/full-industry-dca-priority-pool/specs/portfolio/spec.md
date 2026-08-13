# portfolio Delta Specification

## ADDED Requirements

### Requirement: 资金池视图
系统 SHALL 在黄金坑状态/报告中输出资金池视图：可用现金（扣除现金下限）、当日计划定投总额、实际分配总额、被裁剪的坑位（行业/金额/原因）。

#### Scenario: 查询资金池分配
- **WHEN** 当日存在并发坑位且发生裁剪
- **THEN** 资金池视图 SHALL 包含 planned_amount、actual_amount、cut_items（每个裁剪项含行业 id、优先级、跳过金额）

#### Scenario: 无裁剪
- **WHEN** 当日计划金额未超可用现金
- **THEN** 资金池视图 SHALL 标记 cut_items 为空数组
