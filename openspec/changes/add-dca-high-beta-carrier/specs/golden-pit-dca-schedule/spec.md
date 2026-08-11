## ADDED Requirements

### Requirement: DCA 执行载体解析
对 guide_only 宽基（588000/159915），DCA 执行对象 SHALL 由载体配置（`dca_high_beta_carrier` 能力）决定：`sector_selection` 按板块选筹结果生成 legs，`fixed_combo` 按固定高弹性 ETF 权重生成 legs，`broad` 回退宽基本身。载体灰度开关关闭时 SHALL 维持板块选筹路径。

#### Scenario: 选筹模式维持现状
- **WHEN** 载体模式为 `sector_selection`（灰度默认）
- **THEN** DCA legs SHALL 由 tech7 板块选筹 combo TOP N 结果生成（与现状一致）
- **THEN** 宽基本身 SHALL NOT 生成买入订单

#### Scenario: 固定载体模式生成 legs
- **WHEN** 载体模式为 `fixed_combo` 且灰度开启
- **THEN** `_build_buy_legs` SHALL 按载体 codes/weights 生成买入 legs
- **THEN** 单期 DCA 金额 SHALL 按权重拆分，总额不超 `max_total × 当日权重 × trend_factor`

#### Scenario: 宽基回退模式
- **WHEN** 载体模式为 `broad`
- **THEN** DCA 资金 SHALL 直接买入宽基 ETF 本身（与 guide_only 拆分前行为一致）
- **THEN** 该模式仅用于对照/回滚，SHALL 在日志标记 `/carrier/broad`
