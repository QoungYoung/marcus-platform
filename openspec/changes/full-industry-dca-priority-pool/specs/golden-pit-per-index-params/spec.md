# golden-pit-per-index-params Delta Specification

## ADDED Requirements

### Requirement: 行业级参数配置
系统 SHALL 为全行业池的每个行业提供独立参数：`priority`（资金池裁决优先级）、`max_total`（定投上限）、`min_days_in_pit`（最小入坑天数）、`dca_strategy`（摊投策略）、`proxy_type`（etf/nav 收益代理类型）；与指数参数同存 `golden_pit_etf_config`/`golden_pit_sector_config`，页面弹窗可改。

#### Scenario: 行业优先级影响裁决
- **WHEN** 并发坑位现金不足时
- **THEN** 系统 SHALL 按 `priority` 数值小者优先分配资金（同 tier 内）

#### Scenario: 行业 max_total 上限
- **WHEN** 某行业累计定投已达 `max_total`
- **THEN** 系统 SHALL 停止该行业后续定投（剩余额度 0），资金让位其他坑位

#### Scenario: 场外净值代理
- **WHEN** 行业 `proxy_type=nav`
- **THEN** 系统 SHALL 用 `fund_nav` 单位净值折算买入份额，报告标注为场外代理
