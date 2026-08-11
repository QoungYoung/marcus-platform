## ADDED Requirements

### Requirement: DCA 执行载体配置
系统 SHALL 为每个 guide_only 宽基（588000/159915）提供 DCA 执行载体配置，支持三种模式：`sector_selection`（按 tech7 板块选筹 combo TOP N 分配）、`fixed_combo`（固定高弹性 ETF 等权/加权组合）、`broad`（宽基本身直接买入）。配置 SHALL 存于 PostgreSQL（`golden_pit_sector_config` KV 表），并 SHALL 提供代码常量默认值。

#### Scenario: 科创50 配置固定高弹性组合
- **WHEN** `dca_carrier_588000` 配置为 `{"mode":"fixed_combo","codes":[{"code":"588200","weight":0.5},{"code":"512480","weight":0.5}]}`
- **THEN** 科创50 坑内 DCA 资金 SHALL 按 50/50 等权买入 588200 科创芯片与 512480 半导体
- **THEN** 宽基 588000 本身 SHALL NOT 生成买入订单

#### Scenario: 创业板指配置固定高弹性组合
- **WHEN** `dca_carrier_159915` 配置为 `{"mode":"fixed_combo","codes":[{"code":"159949","weight":1.0}]}`
- **THEN** 创业板指坑内 DCA 资金 SHALL 全部买入 159949 创业板50
- **THEN** 宽基 159915 本身 SHALL NOT 生成买入订单

#### Scenario: 未配置或非法配置回退
- **WHEN** `dca_carrier_<fund>` 未配置或配置非法（mode 未知 / codes 为空 / 权重和不为 1）
- **THEN** 系统 SHALL 回退到 `sector_selection` 模式
- **THEN** 系统 SHALL 在日志中记录回退原因

### Requirement: 载体灰度开关
系统 SHALL 提供 `dca_carrier_enabled` 灰度开关（默认 `false`）。开关关闭时 SHALL 保持现有板块选筹下单行为不变，仅在状态展示中给出目标载体预览；开启后 SHALL 按载体配置实际解析买入 legs。

#### Scenario: 灰度关闭只展示不生效
- **WHEN** `dca_carrier_enabled=false` 且 `dca_carrier_588000` 配置为 fixed_combo
- **THEN** `golden-pit/status` 的 sector_selection 块 SHALL 展示 carrier 字段（enabled=false、目标模式、目标标的）
- **THEN** 实际 DCA 下单 SHALL 仍按现有板块选筹结果执行

#### Scenario: 灰度开启实际生效
- **WHEN** `dca_carrier_enabled=true` 且载体模式为 fixed_combo
- **THEN** `_build_buy_legs` SHALL 按固定载体权重生成买入 legs
- **THEN** 下单日志 strategy 编码 SHALL 包含 `/carrier/fixed_combo` 标记

#### Scenario: 配置回滚
- **WHEN** 灰度开启后表现不佳，将 `dca_carrier_enabled` 置回 false
- **THEN** 系统 SHALL 立即恢复板块选筹执行
- **THEN** 无需改代码或重启服务

### Requirement: 载体金额分配
系统 SHALL 将每期 DCA 金额按载体配置的权重分配到各标的 legs，复用现有 per-leg 下单流程（`_place_buy_order`），并保留 `max_total_amount` 总量上限。

#### Scenario: 多标载体按权重拆分
- **WHEN** fixed_combo 载体为 588200(0.5)+512480(0.5)，当日 DCA 金额为 8000
- **THEN** 588200 与 512480 各分配 4000
- **THEN** 两个 legs 分别下单，金额合计 SHALL 不超过 8000

#### Scenario: 金额不足一手
- **WHEN** 某 leg 金额低于对应 ETF 一手（100 股）所需金额
- **THEN** 该 leg SHALL 跳过并记录日志，金额 SHALL NOT 转移到其他 leg

### Requirement: 载体退出规则
fixed_combo 载体的清仓 SHALL 以对应宽基的窗口退出信号（`full_exit` / `stop_profit` / `fallback_exit`，500 天滚动分位 + 兜底天数）为准，不启用板块连跌/二次拐点退出。sector_selection 模式保留现有板块级退出不变。

#### Scenario: 宽基触发全仓止盈
- **WHEN** 科创50 触发 `full_exit` 且当前载体为 fixed_combo
- **THEN** 588200 与 512480 的全部持仓 SHALL 清仓
- **THEN** 卖出金额 SHALL 按持仓市值比例分配记录到各载体标的

#### Scenario: 板块自身回落不提前退出
- **WHEN** fixed_combo 载体中某标的连续 3 日回落
- **THEN** 系统 SHALL NOT 单独卖出该标的（等待宽基窗口退出信号）
