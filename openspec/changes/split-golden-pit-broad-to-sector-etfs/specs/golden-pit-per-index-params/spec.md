# golden-pit-per-index-params Delta Specification

## ADDED Requirements

### Requirement: Guide-only 指数配置

系统 SHALL 支持在指数配置中标记 `guide_only=true` 的指数（588000 科创50、159915 创业板指）：该指数 SHALL 照常计算入坑检测、拐点确认、退出信号与 ETA 预测，作为板块 ETF 组合的择时指导，但 SHALL NOT 生成宽基本身的买入订单；其 `position_weight` 与 `dca_strategy` 仅作为板块组合的资金节奏与总量参考。

#### Scenario: guide_only 指数保留择时信号

- **WHEN** 588000 配置 `guide_only=true` 且贪婪值跌破 pit_greed
- **THEN** 系统 SHALL 仍输出 golden_pit 状态、days_to_pit/eta_date 预测与拐点确认
- **THEN** 系统 SHALL NOT 对 588000 本身下单

#### Scenario: guide_only 指数参与 DCA 节奏参考

- **WHEN** 588000 处于黄金坑且 `dca_strategy=lump_entry`
- **THEN** 系统 SHALL 以 lump_entry（首日 100%）节奏安排板块 ETF 组合的建仓
- **THEN** 买入标的 SHALL 为选中的板块 ETF 而非 588000

#### Scenario: 非 guide_only 指数行为不变

- **WHEN** 中证500 未配置 `guide_only`
- **THEN** 该指数 SHALL 按现有逻辑直接买入自身 ETF
