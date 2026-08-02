# golden-pit-safety-brake Delta Specification

## Purpose

新增 lump_entry（一次性打入）策略的拐点后反转保护制动规则。

## ADDED Requirements

### Requirement: 一次性打入后反转保护

系统 SHALL 在 lump_entry 策略执行首日买入后，监控后续 3 个交易日内的贪婪值方向。若出现连续 2 天贪婪值下降（反转模式），系统 SHALL 将剩余未投仓位从 lump_entry 切换为 uniform_5 策略继续建仓。

#### Scenario: lump_entry 后 3 天内出现连续 2 天反转

- **WHEN** 创业板指的 `dca_strategy` 为 `lump_entry`
- **WHEN** schedule_day=0 已执行全仓买入
- **WHEN** 次日（day+1）greed 下降，再次日（day+2）greed 继续下降
- **THEN** 系统 SHALL 触发 `lump_reversal` 制动
- **THEN** 剩余未投仓位 SHALL 切换为 `uniform_5` 策略
- **THEN** DCA 日志 SHALL 标记 `status=safety_brake`，`strategy` 包含 `lump_reversal`

#### Scenario: lump_entry 后 greed 正常波动（仅 1 天下降后回升）

- **WHEN** schedule_day=0 已执行全仓买入
- **WHEN** 次日 greed 下降，但再次日 greed 回升
- **THEN** 反转保护 SHALL NOT 触发
- **THEN** 维持原 lump_entry 策略（权重已全部用完，无后续买入）

#### Scenario: lump_entry 后 greed 持续上升

- **WHEN** schedule_day=0 已执行全仓买入
- **WHEN** 后续 3 天 greed 均上升
- **THEN** 反转保护 SHALL NOT 触发
- **THEN** 该窗口标记为正常完成

#### Scenario: uniform_3 等非 lump_entry 策略不检查反转

- **WHEN** 中证1000 的 `dca_strategy` 为 `uniform_3`
- **THEN** 反转保护检查 SHALL NOT 执行
- **THEN** 该制动 SHALL 仅适用于 `dca_strategy == "lump_entry"` 的指数

### Requirement: 反转保护日志可审计

系统 SHALL 在 DCA 日志中记录 lump_entry 反转保护的触发详情，包括触发时的贪婪值序列和切换后的新策略。

#### Scenario: 反转制动被记录

- **WHEN** lump_entry 反转保护触发
- **THEN** DCA 日志 SHALL 包含 `status=safety_brake`
- **THEN** DCA 日志的 `strategy` 字段 SHALL 包含 `lump_reversal/uniform_5`
- **THEN** DCA 日志 SHALL 包含触发时连续 3 天的 greed 值
