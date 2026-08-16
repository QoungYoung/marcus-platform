## Purpose

做T持仓止损的假跌破/洗盘识别：在价格盘中跌破止损价但快速收回（下影线插针、缩量破位、支撑位附近）时避免被洗出底仓，回测与实盘共用同一套纯规则判定。

## ADDED Requirements

### Requirement: 止损收盘确认
做T止损 SHALL 在盘中最低价触及止损价时只记录预警，仅在收盘价 ≤ 止损价时执行卖出（参数关闭时保持现有盘中触发行为）。

#### Scenario: 盘中插针但收盘收回
- **WHEN** 某 bar 最低价 ≤ 止损价 且 当日收盘价 > 止损价
- **THEN** 不执行止损，保留持仓，并记录一次"止损预警-未确认"事件

#### Scenario: 收盘确认止损
- **WHEN** 某 bar 最低价 ≤ 止损价 且 当日收盘价 ≤ 止损价
- **THEN** 按现有止损撮合规则执行卖出

### Requirement: 假跌破收回过滤
当跌破止损价后收盘相对止损价收回幅度 ≥ 配置阈值 `stop_recovery_pct`（默认 1%）时，系统 SHALL 判定为假跌破并跳过本次止损，同时将止损基准重置为该交易日收盘价。

#### Scenario: 收回幅度达标判定假跌破
- **WHEN** bar 最低价 ≤ 止损价 且 收盘价 ≥ 止损价 × (1 + stop_recovery_pct/100)
- **THEN** 不执行止损，止损基准重置为当日收盘价，事件流记录"假跌破-跳过止损"

#### Scenario: 收回幅度不足仍止损
- **WHEN** bar 最低价 ≤ 止损价 且 收盘价 < 止损价 × (1 + stop_recovery_pct/100)
- **THEN** 按收盘确认规则继续判定（收盘 ≤ 止损价则执行止损）

### Requirement: 分钟级企稳确认
启用 `stop_confirm_bars`（默认 5）时，跌破止损价后若连续 N 根 1 分钟 bar 收盘均高于止损价，系统 SHALL 将本次破位标记为假跌破并取消当日止损待确认状态。

#### Scenario: 连续收回取消止损
- **WHEN** 盘中跌破止损价后，连续 stop_confirm_bars 根 1min bar 收盘价 > 止损价
- **THEN** 当日不再对该止损价执行止损，事件流记录"企稳-取消止损"

### Requirement: 缩量破位过滤
启用 `stop_volume_filter` 时，跌破止损价的 bar 成交量低于近 N 日均量的一定比例（默认 0.7）时，系统 SHALL 将该破位标记为疑似洗盘，须同时满足收盘确认与收回幅度才执行止损。

#### Scenario: 缩量破位不立即止损
- **WHEN** 破位 bar 成交量 < 近 N 日均量 × 0.7 且 收盘未同时满足 ≤ 止损价
- **THEN** 不执行止损，等待更强确认

### Requirement: 支撑位感知确认
当止损价与前期低点（近 N 日最低价）或筹码成本峰（`cyq_perf` 成本中位）距离 ≤ 配置阈值（默认 1.5%）时，系统 SHALL 要求更强的确认（收盘确认 + 收回幅度 + 分钟企稳全部满足）才执行止损。

#### Scenario: 支撑位附近破位需全确认
- **WHEN** 止损价位于前期低点/筹码成本峰 1.5% 以内 且 仅满足收盘确认
- **THEN** 不执行止损，等待收回幅度与企稳确认

### Requirement: 守卫参数可配置
假跌破守卫的开关与阈值 SHALL 通过 `t_build_params` 配置（`stop_close_confirm`、`stop_recovery_pct`、`stop_confirm_bars`、`stop_volume_filter`、`stop_support_proximity_pct`），缺省回退内置默认值。

#### Scenario: 参数覆盖生效
- **WHEN** `t_build_params` 中存在守卫参数覆盖值
- **THEN** 回测与实盘均使用覆盖值执行判定

### Requirement: 回测与实盘共用同一守卫
做T回测引擎与实盘止损监控 SHALL 调用同一假跌破判定实现与同一组参数，确保回测结果可代表实盘行为。

#### Scenario: 同参数回测可复现实盘
- **WHEN** 同一标的、同一数据、同一参数在回测与实盘各自触发止损判定
- **THEN** 判定结果一致（数据粒度差异导致的偏差在 caliber_notes 声明）
