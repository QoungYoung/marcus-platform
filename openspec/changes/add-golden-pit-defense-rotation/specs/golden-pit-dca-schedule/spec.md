## MODIFIED Requirements

### Requirement: DCA 基准权重生成
系统 SHALL 在黄金坑信号触发后，根据每个指数在 CHINA_INDICES 中配置的 `dca_strategy` 字段，调用 `_strategy_weights()` 生成 15 天窗口内的每日买入权重向量。系统 SHALL 将每日买入金额按坑内仓位配置拆分：指数自身 90% + 588200（科创芯片）5% + 512480（半导体）5%；增强标的自身贪婪未入坑或数据缺失时，对应份额回退至指数自身。

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

#### Scenario: 坑内买入按 90/5/5 拆分
- **WHEN** 某成长指数触发黄金坑且当日 DCA 买入金额为 X
- **THEN** 指数自身 ETF SHALL 买入 0.9×X
- **THEN** 588200 SHALL 买入 0.05×X
- **THEN** 512480 SHALL 买入 0.05×X

#### Scenario: 增强标的自身未入坑时回退
- **WHEN** 588200 或 512480 自身贪婪状态为 normal（未入坑/未预警）
- **THEN** 对应增强份额 SHALL NOT 买入
- **THEN** 该部分当日金额 SHALL 回退至指数自身买入

#### Scenario: 增强标的历史数据缺失时回退
- **WHEN** 588200 或 512480 无贪婪历史（不足 60 天）
- **THEN** 对应增强份额 SHALL NOT 买入
- **THEN** 该部分金额 SHALL 并入指数自身买入