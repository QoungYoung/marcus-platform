# golden-pit-dca-schedule Delta Specification

## MODIFIED Requirements

### Requirement: DCA 基准权重生成

系统 SHALL 在黄金坑信号触发后，根据每个指数在 CHINA_INDICES 中配置的 `dca_strategy` 字段，调用 `_strategy_weights()` 生成 15 天窗口内的每日买入权重向量。对于 `guide_only` 指数（588000/159915），该权重向量 SHALL 仅作为板块 ETF 组合的资金投放节奏参考，当日买入金额 SHALL 按所选板块的 combo 分数权重拆分到板块 ETF，而非买入宽基本身。

#### Scenario: uniform_3 策略生成权重

- **WHEN** 中证1000 的 `dca_strategy` 为 `uniform_3`
- **THEN** 权重向量 SHALL 为 `[0.333, 0.333, 0.333, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]`
- **THEN** 前 3 天每日建仓目标为 `max_total × 0.333 × trend_factor`

#### Scenario: lump_entry 策略生成权重

- **WHEN** 科创50（guide_only）的 `dca_strategy` 为 `lump_entry`
- **THEN** 权重向量 SHALL 为 `[1.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]`
- **THEN** 首日建仓目标为 `max_total × 1.0 × trend_factor`，买入标的为所选板块 ETF

#### Scenario: 未知策略回退

- **WHEN** `dca_strategy` 为空或不在已知策略列表中
- **THEN** 系统 SHALL 回退为 `uniform_10`（前 10 天等权重）

#### Scenario: guide_only 当日金额按板块权重拆分

- **WHEN** 588000 触发黄金坑且当日 DCA 计划金额为 X
- **THEN** 系统 SHALL 按板块 combo 权重将 X 拆分至各选中板块 ETF
- **THEN** 系统 SHALL NOT 对 588000 本身下单

## ADDED Requirements

### Requirement: 坑内组合买入执行

系统 SHALL 在 guide_only 指数处于黄金坑窗口时，将当日 DCA 金额解析为板块 ETF 订单：订单标的为 `SECTOR_ETF_POOL` 中 combo 信号选中的板块 ETF，金额 = `max_total × dca_weight[schedule_day] × sector_weight × trend_factor`。

#### Scenario: 多板块同时建仓

- **WHEN** 当日选中 半导体 与 通信设备 两个板块（权重 70%/30%）
- **THEN** 系统 SHALL 生成 512480 订单（金额 0.7 × 当日 DCA 金额）
- **THEN** 系统 SHALL 生成 515880 订单（金额 0.3 × 当日 DCA 金额）

#### Scenario: 板块信号失效跳过当日买入

- **WHEN** 当日所有候选板块 combo 信号均不满足门槛
- **THEN** 系统 SHALL 跳过当日买入
- **THEN** schedule_day SHALL NOT 递增（跳过日不计入窗口进度）
