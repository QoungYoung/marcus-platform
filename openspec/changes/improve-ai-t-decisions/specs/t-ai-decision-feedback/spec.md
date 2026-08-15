## Purpose

AI 做T决策的反馈闭环与质量度量：决策结果回填（outcome：成交价/后续走向/实际盈亏）、决策质量指标（exec 胜率/abandon 正确率/wait 转化）、带历史统计的决策上下文，让 AI 基于真实行为模式迭代判断，提升做T收益率。

## ADDED Requirements

### Requirement: 决策结果回填（outcome）
系统 SHALL 在 AI 决策（exec）成交后回填实际结果到审计记录：成交价、成交后 N 根 bar 的走向（涨/跌/幅度/是否达目标价/是否触及止损）、实际盈亏（高抛实盈/低吸后续评估）。回填数据写入 `t_ai_actions.outcome`，与决策输入输出关联可追溯。

#### Scenario: 成交后回填 outcome
- **WHEN** AI 决策 exec 且网关成交（实盘撮合或回测撮合）
- **THEN** 系统在成交后按该标的后续行情回填 outcome（成交价/后续走向/实际盈亏）到对应 t_ai_actions 记录

#### Scenario: 非成交决策无 outcome
- **WHEN** AI 决策 wait/abandon/update_condition 未成交
- **THEN** 不回填 outcome（wait 可回填"随后是否再次触发"；abandon 可回填"放弃后行情验证"用于正确率统计）

### Requirement: 决策质量指标
系统 SHALL 统计并输出 AI 决策质量指标：exec 胜率（放行的成交中实际盈利占比）、abandon 正确率（放弃后行情下跌占比 = 放弃正确；上涨 = 错杀）、wait 转 exec 比例、exec 平均盈亏、各决策动作分布。指标按标的/日期/条件维度可查，回测输出到报告、实盘输出到 `/t/ai/actions`。

#### Scenario: 回测报告含决策质量指标
- **WHEN** 回测任务完成
- **THEN** 报告 metrics 包含 exec 胜率、abandon 正确率、wait/exec/abandon 分布与平均盈亏

#### Scenario: 实盘决策质量可查
- **WHEN** 查询 /t/ai/actions
- **THEN** 返回按标的聚合的 exec 胜率与 abandon 正确率（基于已回填 outcome 的记录）

### Requirement: 带历史统计的决策上下文
系统 SHALL 在唤醒 AI 决策时提供标的做T历史统计：最近 N 次决策及各自结果（outcome）、该标的低吸触发后近 M 次的实际走向统计（平均涨幅/胜率/达目标价率）、exec 历史胜率；并在决策提示中给出强化 checklist（价差盈亏比、底仓弹药、历史模式、连续命中行为）。

#### Scenario: 唤醒上下文含历史结果
- **WHEN** 某标的触发唤醒 AI 决策
- **THEN** 上下文包含该标的最近 5 次决策及结果（含 outcome 摘要）与该标的做T行为统计（低吸后走向/胜率）

#### Scenario: 决策 checklist 提示
- **WHEN** AI 收到触发上下文
- **THEN** 提示包含决策 checklist：触发价 vs 现价 vs 目标价的盈亏比、底仓可卖弹药与浮盈浮亏、该标的低吸历史胜率与平均走向、连续命中计数及调整/冷却要求
