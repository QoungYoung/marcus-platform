# golden-pit-dca-schedule Specification

## Purpose
TBD - created by archiving change reliable-batch-position-building. Update Purpose after archive.
## Requirements
### Requirement: DCA 基准权重生成
系统 SHALL 在黄金坑信号触发后，根据每个指数在 CHINA_INDICES 中配置的 `dca_strategy` 字段，调用 `_strategy_weights()` 生成 15 天窗口内的每日买入权重向量。

#### Scenario: uniform_3 策略生成权重
- **WHEN** 中证1000 的 `dca_strategy` 为 `uniform_3`
- **THEN** 权重向量 SHALL 为 `[0.333, 0.333, 0.333, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]`
- **THEN** 前 3 天每日建仓目标为 `max_total × 0.333 × trend_factor`

#### Scenario: lump_entry 策略生成权重
- **WHEN** 科创50 的 `dca_strategy` 为 `lump_entry`
- **THEN** 权重向量 SHALL 为 `[1.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]`
- **THEN** 首日建仓目标为 `max_total × 1.0 × trend_factor`

#### Scenario: 未知策略回退
- **WHEN** `dca_strategy` 为空或不在已知策略列表中
- **THEN** 系统 SHALL 回退为 `uniform_10`（前 10 天等权重）

### Requirement: DCA 窗口进度追踪
系统 SHALL 在每次 DCA 执行时追踪当前处于窗口的第几天（`schedule_day`），从信号触发日算起（day=0），并将该信息持久化到 DCA 日志中。

#### Scenario: 信号触发首日
- **WHEN** 贪婪值首次跌破 `pit_greed` 触发信号
- **THEN** `schedule_day` SHALL 设为 0
- **THEN** 当日买入金额 SHALL = `dca_weight[0] × trend_factor × max_total`

#### Scenario: 窗口中期执行
- **WHEN** 信号触发后第 3 天执行 DCA
- **THEN** `schedule_day` SHALL 为 2
- **THEN** 当日买入金额 SHALL = `dca_weight[2] × trend_factor × max_total`

#### Scenario: 跳过某天的执行
- **WHEN** 某天因安全制动被跳过
- **THEN** `schedule_day` SHALL NOT 递增（跳过日不计入窗口进度）
- **THEN** 该天的 DCA 权重 SHALL NOT 累积到后续日

### Requirement: DCA 窗口超时兜底
系统 SHALL 为每个指数设置 `dca_fallback` 天数上限。当 `schedule_day` 超过此上限时，系统 SHALL 取消趋势因子压制（trend_factor 强制=1.0），按 DCA 基准权重完成剩余计划内的买入。

#### Scenario: 底部长期震荡未确认拐点
- **WHEN** 恒生指数的 `dca_fallback` 为 15 天
- **WHEN** 信号触发后 15 天内 trend 始终为 declining
- **THEN** 第 16 天起 trend_factor SHALL 强制=1.0
- **THEN** 系统 SHALL 在剩余 DCA 计划日内完成买入

### Requirement: 二次信号窗口重置
系统 SHALL 在 DCA 窗口内检测贪婪值是否创出新低。若当前贪婪值比信号触发日的贪婪值低超过 5%（相对比例），则触发窗口重置。

#### Scenario: 贪婪继续创新低触发重置
- **WHEN** 信号触发日 greed=0.380
- **WHEN** DCA 窗口第 2 天 greed=0.355（低于 0.380 × 0.95 = 0.361）
- **THEN** `schedule_day` SHALL 重置为 0
- **THEN** DCA 权重 SHALL 从第 0 天重新开始
- **THEN** 累计投入上限 SHALL NOT 重置（保持原 max_total）

#### Scenario: 最多重置一次
- **WHEN** 窗口已被重置过一次
- **THEN** 不再检测二次信号重置条件
- **THEN** 系统 SHALL 维持当前进度直到 `dca_fallback` 触发

