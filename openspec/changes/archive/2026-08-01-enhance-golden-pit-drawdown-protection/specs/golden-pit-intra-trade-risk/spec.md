# golden-pit-intra-trade-risk Specification

## Purpose

追踪黄金坑回测中每笔交易持有期内的最大浮亏（Maximum Adverse Excursion），弥补现有回测仅报告最终离场收益的盲区。

## ADDED Requirements

### Requirement: 回测追踪 intra-trade MAE

系统 SHALL 在 `simulate_dca()` 的逐日持有期遍历中，追踪入场后至退场前的最低收盘价，并在交易退出时计算 `max_adverse_excursion = (min_close / avg_entry_price - 1)`。

#### Scenario: 单笔交易追踪持有期内最低点

- **WHEN** 某笔交易以 avg_entry=10.0 入场
- **WHEN** 持有期内最低收盘价为 8.0
- **WHEN** 最终退出价格为 9.5
- **THEN** `max_adverse_excursion` SHALL 为 -0.20（-20%）
- **THEN** `return` SHALL 为 -0.05（-5%）
- **THEN** 两个字段 SHALL 同时存在于 trade dict 中

#### Scenario: 持有期内价格从未低于入场价

- **WHEN** avg_entry=10.0，持有期内最低收盘价为 10.2
- **THEN** `max_adverse_excursion` SHALL 为 +0.02（正值，表示从未浮亏）

### Requirement: MAE 分布统计报告

系统 SHALL 在回测报告的 `compute_metrics()` 中，统计所有交易的 MAE 分布：MAE < -5%、< -10%、< -15%、< -20%、< -30% 的交易数量和占比。

#### Scenario: 回测报告包含 MAE 分布

- **WHEN** 某指数回测产生 53 笔交易
- **WHEN** 其中 14 笔的 MAE < -10%
- **THEN** 报告 SHALL 包含 `mae_distribution: {"p5": 28, "p10": 14, "p15": 8, "p20": 5, "p30": 0}`
- **THEN** 报告 SHALL 同时包含 MAE 的 mean、median、min、max

#### Scenario: 交易数不足时跳过 MAE 分布

- **WHEN** 某指数交易数 < 3
- **THEN** MAE 分布统计 SHALL 为 null
- **THEN** 不影响其他指标的返回
