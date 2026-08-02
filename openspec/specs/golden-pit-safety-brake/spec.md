# golden-pit-safety-brake Specification

## Purpose
TBD - created by archiving change reliable-batch-position-building. Update Purpose after archive.
## Requirements
### Requirement: 假信号暂停
系统 SHALL 在 DCA 窗口期内检测贪婪值是否回升到 `entry_greed` 以上。一旦突破，系统 SHALL 暂停该指数的当日买入并重置该指数的买入状态。

#### Scenario: 贪婪快速回升触发假信号暂停
- **WHEN** 科创50 greed=0.41，其 `entry_greed` 为 0.40
- **WHEN** DCA 窗口第 1 天（schedule_day=0）
- **THEN** 当日买入 SHALL 被跳过
- **THEN** 跳过原因 SHALL 标记为 `fake_signal`
- **THEN** 该指数的 DCA 窗口 SHALL 被标记为 `aborted`

#### Scenario: 贪婪仍在阈值以下继续执行
- **WHEN** 中证500 greed=0.38，其 `entry_greed` 为 0.395
- **THEN** 假信号检测 SHALL NOT 触发
- **THEN** 正常执行 DCA 买入

### Requirement: 飞刀保护
系统 SHALL 在 DCA 窗口期内检测单日贪婪值跌幅。若当日贪婪值较前一交易日下跌超过 2%（绝对值），系统 SHALL 跳过当日买入。

#### Scenario: 单日大幅下跌触发飞刀保护
- **WHEN** 前一交易日 greed=0.380，当日 greed=0.355（跌幅 2.5 个百分点）
- **THEN** 当日买入 SHALL 被跳过
- **THEN** 跳过原因 SHALL 标记为 `falling_knife`
- **THEN** `schedule_day` SHALL NOT 递增

#### Scenario: 正常波动不触发保护
- **WHEN** 前一交易日 greed=0.380，当日 greed=0.375（跌幅 0.5 个百分点）
- **THEN** 飞刀保护 SHALL NOT 触发
- **THEN** 正常执行 DCA 买入

### Requirement: 累计投入硬截断
系统 SHALL 确保每个指数在当前 DCA 窗口内的累计投入绝对不超过 `max_total_amount`。

#### Scenario: 累计达到上限自动停止
- **WHEN** 某指数 `max_total_amount` 为 100,000
- **WHEN** 当前窗口累计已投入 98,000
- **WHEN** 当日计算金额为 5,000
- **THEN** 实际执行金额 SHALL 被截断为 2,000
- **THEN** `schedule_day` SHALL 不再递增
- **THEN** 该指数 SHALL 标记为 `completed`

### Requirement: 制动日志可审计
系统 SHALL 在 DCA 日志中记录每次安全制动触发的类型和原因。

#### Scenario: 安全制动被记录
- **WHEN** 飞刀保护跳过当日买入
- **THEN** DCA 日志 SHALL 包含 `status=safety_brake`
- **THEN** DCA 日志的 `strategy` 字段 SHALL 包含 `safety_brake/falling_knife`
- **THEN** 日志 SHALL 包含触发的贪婪值和阈值

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

