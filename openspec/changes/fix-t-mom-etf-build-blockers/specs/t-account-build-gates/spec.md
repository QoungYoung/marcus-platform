## Purpose

Define the t-account position-building gate behavior shared by all build sources (daily auto-select, V-rebound, trend breakout, momentum ETF): automated builds never pause for human confirmation, and the daily build quota counts only actually executed builds.

## ADDED Requirements

### Requirement: 自动建仓不要求人工确认

t 账户所有自动建仓来源（agent/daily_auto/ai_led）的建仓尝试 SHALL 直接自动放行，不因 regime=CAUTIOUS、首开新标的、单笔超标准档、连续亏损期、日亏预警、近跌停、当日触犯建仓风控（B1/B2/B4/B5/B6/B7/B8）等升级为 human_confirm 暂停等待；上述风险分类 SHALL 作为告警信息写入建仓结果与日志。HALT（B3）熔断 SHALL 仍强制拦截一切建仓（含人工）。

#### Scenario: 谨慎档自动放行
- **WHEN** 盘中 regime=CAUTIOUS 且某自动建仓来源尝试建仓
- **THEN** 系统 SHALL 直接执行建仓（通过其余护栏时），不产生 human_confirm 事件，风险原因 SHALL 记入告警

#### Scenario: 首开新标的不暂停
- **WHEN** t 账户从未持有的新标的被自动来源选为建仓目标
- **THEN** 系统 SHALL 直接建仓，不要求人工确认

#### Scenario: 连续亏损/日亏预警不暂停
- **WHEN** 账户处于连续亏损期或接近日亏预警线且自动来源尝试建仓
- **THEN** 系统 SHALL 仍自动放行，风险原因 SHALL 记录为告警

#### Scenario: HALT 仍强制拦截
- **WHEN** regime=HALT（全局熔断）
- **THEN** 系统 SHALL 拒绝一切建仓（含人工来源），不自动放行

- **REQ-GATE-001** 自动建仓来源（agent/daily_auto/ai_led）的建仓尝试不因升级分类 B1/B2/B4/B5/B6/B7/B8 暂停为 human_confirm，一律自动放行；升级分类结果 SHALL 以告警形式随建仓结果与日志输出。
- **REQ-GATE-002** regime=HALT（B3）时 SHALL 拒绝一切建仓，该拦截不因本变更放宽。
- **REQ-GATE-003** 系统 SHALL 不再为自动建仓来源创建 status='human_confirm' 的建仓事件；人工确认端点仅用于处理历史遗留事件。

### Requirement: 当日建仓配额只计已成交

当日建仓配额（自动 ≤3/人工 ≤5）与单票当日建仓限制 SHALL 只统计当日 status='executed' 的建仓事件；human_confirm/pending/rejected/cancelled 等未成交事件 SHALL 不消耗当日配额。

#### Scenario: 未成交事件不占当日配额
- **WHEN** 当日存在 human_confirm 或 pending 状态的未成交建仓事件
- **THEN** 系统 SHALL 仍可继续自动建仓，当日配额只按已成交笔数计算

#### Scenario: 单票当日限制只计成交
- **WHEN** 当日某标的建仓未成交（被拒/升级后取消）后再次成为目标
- **THEN** 系统 SHALL 允许当日再次尝试该标的建仓（单票当日限制只计已成交笔数）

- **REQ-GATE-004** 当日建仓配额（自动/人工来源）只统计当日 status='executed' 的建仓事件；未成交事件不消耗配额。
- **REQ-GATE-005** 单票当日建仓限制同样只统计当日该标的 status='executed' 的建仓笔数。
