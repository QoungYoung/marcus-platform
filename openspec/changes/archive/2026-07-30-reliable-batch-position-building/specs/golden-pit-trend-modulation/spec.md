## ADDED Requirements

### Requirement: 趋势状态到调节因子的映射
系统 SHALL 将当前的趋势状态（由 `_detect_trend()` 返回的 `trend` 和 `days_rising`）映射为连续的趋势调节因子，用于缩放 DCA 基准权重。

#### Scenario: declining 状态减速建仓
- **WHEN** 指数的 `trend` 为 `declining` 且 `days_rising` 为 0
- **THEN** 趋势因子 SHALL 为 0.1
- **THEN** 当日买入金额 SHALL = `dca_weight[day] × 0.1 × max_total`

#### Scenario: bottoming 状态初步试探
- **WHEN** 指数的 `days_rising` 为 1（首次回升）
- **THEN** 趋势因子 SHALL 为 0.5
- **THEN** 当日买入金额 SHALL = `dca_weight[day] × 0.5 × max_total`

#### Scenario: turning 状态基准建仓
- **WHEN** 指数的 `days_rising` 为 2 且 `turning_point_confirmed` 为 True
- **THEN** 趋势因子 SHALL 为 1.0
- **THEN** 当日买入金额 SHALL = `dca_weight[day] × 1.0 × max_total`

#### Scenario: accelerating 状态加速建仓
- **WHEN** 指数的 `days_rising` 为 3
- **THEN** 趋势因子 SHALL 为 1.2
- **THEN** 当日买入金额 SHALL = `dca_weight[day] × 1.2 × max_total`
- **THEN** 金额 SHALL NOT 超过 `max_total - total_invested`

#### Scenario: full 状态快速满仓
- **WHEN** 指数的 `days_rising` ≥ 4
- **THEN** 趋势因子 SHALL 为 1.5
- **THEN** 当日买入金额 SHALL = `dca_weight[day] × 1.5 × max_total`

### Requirement: 趋势因子加速阈值保护
当贪婪值已回升到 `entry_greed` 以上时，系统 SHALL 限制趋势因子上限为 1.0，防止在脱离黄金坑后追高。

#### Scenario: 贪婪回升后禁止加速
- **WHEN** 当前 greed=0.42，指数 `entry_greed` 为 0.40
- **WHEN** `days_rising` = 4（原本因子=1.5）
- **THEN** 趋势因子 SHALL 被限制为 1.0
- **THEN** 当日买入金额 SHALL = `dca_weight[day] × 1.0 × max_total`

### Requirement: 分指数趋势因子覆盖
系统 SHALL 支持每个指数在 CHINA_INDICES 中定义自己的 `trend_factors` 字典，用于覆盖全局默认值。未定义的键 SHALL 回退到全局默认值。

#### Scenario: 指数自定义趋势因子
- **WHEN** 中证1000 的 `trend_factors` 为 `{"declining": 0.15, "full": 1.3}`
- **WHEN** 趋势状态为 declining
- **THEN** 趋势因子 SHALL 为 0.15（使用覆盖值）
- **WHEN** 趋势状态为 turning
- **THEN** 趋势因子 SHALL 为 1.0（使用全局默认值，因未覆盖）

### Requirement: 趋势因子叠加顺序
系统 SHALL 按固定顺序应用所有仓位调节因子，确保一致性和可审计性。

#### Scenario: 完整叠加顺序
- **WHEN** 计算最终买入金额
- **THEN** 叠加顺序 SHALL 为：`max_total × dca_weight × trend_factor × position_multiplier × resonance × macro_coefficient`
- **THEN** 最终金额 SHALL NOT 超过 `max_total - total_invested`
- **THEN** 最终金额 SHALL NOT 小于 0
