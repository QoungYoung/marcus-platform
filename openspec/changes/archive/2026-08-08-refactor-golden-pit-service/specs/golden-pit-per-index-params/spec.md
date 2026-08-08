## ADDED Requirements

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

