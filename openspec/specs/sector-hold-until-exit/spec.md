## Purpose

板块选筹采用"只截新入"持仓保留语义：已持仓板块在未触发退出规则前保留在目标组合中，不被每日 TOP N 重排替换；仅对新进入候选做数量截断。解决生产当前"持仓板块跌出 TOP N 当日即停止买入"导致的频繁换手与牛市踏空问题。

## Requirements

### Requirement: 持仓保留的选筹输出

系统 SHALL 在配置 `hold_until_exit` 开启时，将调用方传入的当前板块持仓合并进目标组合：已持仓板块保留（不参与 TOP N 截断），新进入候选按排序截断到 `top_n`；持仓保留权重按现有归一化逻辑处理。

#### Scenario: 持仓板块跌出当日 TOP N

- **WHEN** 某持仓板块当日 combo 排名跌出 TOP N，但未触发板块退出规则（连 `exit_down_days` 日回落/宽基坑结束）
- **THEN** 该板块 SHALL 仍保留在 `select_sectors` 返回的 `selected` 中（按持仓保留权重）
- **AND** 新进入候选 SHALL 按 combo 排序截断，优先让位给持仓板块

#### Scenario: 配置关闭时保持原语义

- **WHEN** `hold_until_exit=false`
- **THEN** 选筹 SHALL 保持现有全量重排语义（仅保留当日 TOP N，不合并持仓）

#### Scenario: 无持仓输入

- **WHEN** 调用方未提供持仓（`holdings` 为空）
- **THEN** 选筹 SHALL 退化为现有全量 TOP N 逻辑，不报错

### Requirement: DCA 持仓传入与退出协同

系统 SHALL 在 DCA 执行买入时，把 `guide_only` 宽基名下当前板块模拟持仓（`_get_sector_holdings`）传入选筹；持仓板块的退出仍由既有退出规则（板块连 `exit_down_days` 日回落提前卖、宽基退出组合级清仓）驱动，`hold_until_exit` 只影响"是否继续买入"，不影响"是否卖出"。

#### Scenario: 持仓传入选筹

- **WHEN** 板块拆分启用（`enabled=true`）且 `hold_until_exit=true`
- **THEN** `_build_buy_legs` SHALL 调用 `select_sectors(holdings=当前板块持仓)` 获取目标组合
- **AND** 持仓板块未触发退出时 SHALL 继续获得当日买入金额（按保留权重）

#### Scenario: 退出规则优先

- **WHEN** 持仓板块触发板块二次拐点退出（连续回落 `exit_down_days` 天）
- **THEN** 该板块 SHALL 从持仓保留集合中移除，不再被 `hold_until_exit` 保留买入
