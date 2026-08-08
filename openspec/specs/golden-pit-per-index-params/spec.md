## Purpose

Define per-index parameters for golden pit strategy: entry/exit thresholds, position sizing multipliers, resonance coefficients, and the global macro coefficient overlay for position sizing.
## Requirements
### Requirement: Per-index entry and exit thresholds
Each tracked broad-market index SHALL have its own configuration for entry percentile threshold, exit percentile thresholds, turning point confirmation days, DCA strategy, trend factor overrides, and DCA fallback days, replacing global constants where applicable.

#### Scenario: Different entry thresholds per index
- **WHEN** the system evaluates golden pit status for 科创50 (high elasticity)
- **THEN** the entry warning threshold SHALL be P15 (more aggressive early entry)
- **WHEN** the system evaluates golden pit status for 沪深300 (low elasticity)
- **THEN** the entry warning threshold SHALL be P5 (more conservative, only enter deep pits)

#### Scenario: Different turning point confirmation days
- **WHEN** the system detects trend for 科创50 (fast recovery)
- **THEN** turning point confirmation SHALL require 1 consecutive rising day
- **WHEN** the system detects trend for 沪深300 (slow recovery)
- **THEN** turning point confirmation SHALL require 2 consecutive rising days

#### Scenario: Different DCA strategies per index
- **WHEN** the system executes DCA for 科创50 (high win rate, strong trend)
- **THEN** `dca_strategy` SHALL be `lump_entry` (100% day 1)
- **WHEN** the system executes DCA for 中证1000 (high volatility, lower win rate)
- **THEN** `dca_strategy` SHALL be `uniform_3` (33% over 3 days)

### Requirement: Per-index position sizing multipliers
Each index SHALL have configurable position sizing multipliers that scale the base POSITION_TIERS percentages according to the index's historical return characteristics.

#### Scenario: Higher position weight for strong-signal indices
- **WHEN** 科创50 triggers a position tier of "turning" (base 50%)
- **THEN** the actual position SHALL be 50% × 1.2 = 60% of max_total (strong signal multiplier)
- **WHEN** 沪深300 triggers a position tier of "turning" (base 50%)
- **THEN** the actual position SHALL be 50% × 0.8 = 40% of max_total (defensive multiplier)

#### Scenario: Pre-turn cap varies by index
- **WHEN** the system calculates pre-turn cumulative cap for 科创50
- **THEN** the cap SHALL be max_total × 20% (higher cap for high-conviction index)
- **WHEN** the system calculates pre-turn cumulative cap for 沪深300
- **THEN** the cap SHALL be max_total × 10% (lower cap for defensive index)

### Requirement: Multi-index resonance coefficient
The DCA execution service SHALL apply a resonance multiplier to order amounts based on how many indices are simultaneously in golden pit status, reflecting stronger signal confidence when multiple indices confirm.

#### Scenario: Strong resonance with 4+ indices
- **WHEN** 4 or more tradeable indices have status="golden_pit"
- **THEN** the resonance multiplier SHALL be 1.3
- **THEN** individual order amounts SHALL be multiplied by 1.3, capped at max_total_amount

#### Scenario: Weak resonance with single index
- **WHEN** only 1 tradeable index has status="golden_pit" or "warning"
- **THEN** the resonance multiplier SHALL be 0.6
- **THEN** individual order amounts SHALL be multiplied by 0.6

#### Scenario: No resonance effect in idle phase
- **WHEN** the golden pit window phase is "idle"
- **THEN** the resonance multiplier SHALL be 1.0 (no effect, DCA is already skipped)

### Requirement: Global macro coefficient in position sizing
Each index's position sizing calculation SHALL incorporate a `global_macro_coefficient` multiplier derived from the global capital flow sentiment score, applied after the existing resonance multiplier. This coefficient SHALL be computed once per DCA execution cycle and applied uniformly to all indices.

#### Scenario: Global coefficient applies after resonance
- **WHEN** the DCA service calculates an order amount for any index
- **THEN** the effective amount SHALL be `max_total × tier_pct × position_multiplier × resonance × global_macro_coefficient`
- **THEN** the coefficient SHALL NOT cause the cumulative investment to exceed `max_total_amount`

#### Scenario: Coefficient range validation
- **WHEN** the global_macro_coefficient is computed
- **THEN** its value SHALL be in the range [0, 1.5]
- **THEN** values outside this range SHALL be clamped

### Requirement: Per-index trend factor overrides
Each index SHALL support an optional `trend_factors` dictionary that overrides the global default trend-to-factor mapping, allowing different indices to have different acceleration/deceleration profiles.

#### Scenario: Custom declining factor for volatile index
- **WHEN** 中证1000 has `trend_factors` = `{"declining": 0.15, "full": 1.3}`
- **WHEN** trend state is `declining`
- **THEN** trend factor SHALL be 0.15 (override) instead of global default 0.1

#### Scenario: Global default fallback for uncovered states
- **WHEN** 中证1000 has `trend_factors` = `{"declining": 0.15}` only
- **WHEN** trend state is `turning`
- **THEN** trend factor SHALL be 1.0 (global default, since turning is not overridden)

#### Scenario: No trend_factors defined
- **WHEN** an index has no `trend_factors` key in CHINA_INDICES
- **THEN** ALL trend states SHALL use global defaults

### Requirement: Per-index DCA fallback days
Each index SHALL have a `dca_fallback` field that defines the maximum number of calendar days the DCA schedule window can span. If the window exceeds this limit without completing the schedule (due to safety brakes or persistent declining trend), the system SHALL force-complete remaining planned buys.

#### Scenario: DCA fallback triggers force completion
- **WHEN** 恒生指数 has `dca_fallback` = 15 and schedule has been active for 16 days
- **WHEN** trend factor is still suppressed (declining state)
- **THEN** trend factor SHALL be forced to 1.0
- **THEN** remaining DCA schedule days SHALL execute at full weight

#### Scenario: Short fallback for lump_entry indices
- **WHEN** 科创50 has `dca_fallback` = 5 and `dca_strategy` = `lump_entry`
- **THEN** if lump_entry fails to execute on day 0 due to safety brake
- **THEN** the system SHALL attempt full execution by day 5
### Requirement: 入坑 ETA 预测与状态判定基准一致

系统 SHALL 使 `days_to_pit`/`eta_date` 预测与状态判定使用同一套阈值基准：`use_fixed_greed=True` 的指数 SHALL 用固定 `pit_greed` 计算距入坑天数；`use_fixed_greed=False` 的指数 SHALL 用滚动窗口 P(pit_pct) 值计算。预测结果 SHALL 与 `_determine_status` 的判定口径一致。

#### Scenario: 固定阈值指数按 pit_greed 预测
- **WHEN** 科创50 处于 warning（greed=0.360，pit_greed=0.348）
- **THEN** `days_to_pit` SHALL 基于 greed 降至 pit_greed 所需天数计算，而非滚动 P5 值

#### Scenario: 百分位指数按 P5 预测
- **WHEN** 道琼斯指数（use_fixed_greed=False）处于 warning
- **THEN** `days_to_pit` SHALL 基于当前贪婪值降至滚动窗口 P(pit_pct) 值所需天数计算

### Requirement: v1 综合评分只统计可交易指数

系统 SHALL 使 `get_score` 的最低分位统计只包含 `core`/`satellite`/`defense` 层级的指数，排除 `drop` 与 `watch` 层级。

#### Scenario: 放弃级指数低分位不拉高评分
- **WHEN** 上证50（tier=drop）处于 P1 而所有可交易指数处于 P50
- **THEN** `get_score` 的评分 SHALL 基于可交易指数的最低分位（P50）计算
