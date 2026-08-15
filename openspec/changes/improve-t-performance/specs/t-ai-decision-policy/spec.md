## Purpose

AI 做T决策策略能力：将 AI 决策从保守偏置改为盈亏比导向，高抛兑现视为正向动作；解析失败/异常时按规则兜底而非一律等待；高胜率标的放开重触发，提升决策执行质量与做T实盈。

## ADDED Requirements

### Requirement: 盈亏比导向决策 checklist
系统 SHALL 在唤醒 AI 决策的提示中提供盈亏比导向 checklist：现价距目标价 ≥0.5% 且覆盖成本（滑点+手续费）才倾向 exec；目标离收益空间 / 潜在回撤空间 ≥1.2 才执行；高抛（卖腿）触发视为兑现利润的正向动作，不应被无条件保守话术压制。

#### Scenario: 高抛触发时 AI 倾向 exec
- **WHEN** 高抛触发且现价距高抛目标价 ≥0.5%、盈亏比 ≥1.2
- **THEN** 提示引导 AI 输出 exec 卖出兑现，而非等待或放弃

#### Scenario: 薄价差无利润空间时放弃
- **WHEN** 触发价与目标价价差 <0.5% 或覆盖成本后无利润空间
- **THEN** 提示引导 AI 输出 abandon，并说明价差不足理由

### Requirement: 解析失败规则兜底
系统 SHALL 在 AI 回复解析失败（空/非法/非白名单动作）时，不静默 wait，而是按规则评审（regime 是否 HALT、连亏、无底仓、日亏预警）生成默认动作：高抛触发在 HALT/CAUTIOUS 下默认 exec，低吸默认 wait；兜底决策在审计记录的 reason 中标注 `[rule_fallback]`。

#### Scenario: 高抛触发解析失败走兜底 exec
- **WHEN** 高抛触发且 AI 回复无法解析
- **THEN** 系统按规则兜底执行卖出兑现，审计 reason 含 `[rule_fallback]`

#### Scenario: 低吸触发解析失败走兜底 wait
- **WHEN** 低吸触发且 AI 回复无法解析、regime 非 HALT
- **THEN** 系统按规则兜底 wait，审计 reason 含 `[rule_fallback]`，不自动买入

### Requirement: 高胜率标的重触发放开
系统 SHALL 对 exec 历史胜率 >55% 的标的放开连续命中冷却：不再强制 update_condition 冷却，允许更多次同方向触发（仍受单条件日触发上限约束）；对胜率 <40% 的标的缩短冷却并提示减仓。

#### Scenario: 高胜率标的连续触发不被冷却
- **WHEN** 标的 exec 历史胜率 60% 且连续 3 次命中
- **THEN** 系统不再强制冷却，允许继续按条件触发，审计不出现冷却记录

#### Scenario: 低胜率标的提示减仓
- **WHEN** 标的 exec 历史胜率 35%
- **THEN** 决策上下文提示该标的历史胜率低、建议减仓或收紧触发
