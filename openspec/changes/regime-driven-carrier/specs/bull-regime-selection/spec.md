# bull-regime-selection Specification

## Purpose

牛熊状态驱动的选筹/载体模式切换：基于 `golden-pit/tech-status` 的趋势腿激活数（`trend_up_count`），在超跌（贪婪）选筹、趋势（高弹性载体/动量）、宽基躺平三种模式间自动切换，解决"超跌选筹在牛市跑输躺平/趋势"的回测结论在生产未落地的问题。

## MODIFIED Requirements

### Requirement: 选筹/载体模式配置

系统 SHALL 提供 `regime_mode` 配置（`auto`/`oversold`/`trend`/`bh`）与 `regime_trend_threshold`（默认 5）：
- `oversold`：固定超跌（greed）选筹，执行载体 `sector_selection`
- `trend`：固定趋势模式，执行载体 `fixed_combo`（高弹性组合；`select_sectors(mode=trend)` 动量选筹保留为展示/备用路径）
- `bh`：固定宽基躺平，执行载体 `broad`
- `auto`：读取科技现状趋势腿激活数，≥ 阈值解析为 `trend`，否则 `oversold`；`regime_carrier_enabled=true` 时解析结果直接决定执行载体

#### Scenario: auto 模式阈值切换

- **WHEN** `regime_mode=auto` 且 `trend_up_count >= regime_trend_threshold`
- **THEN** regime 解析为 `trend`，`regime_carrier_enabled=true` 时执行载体为 `fixed_combo`
- **AND** 否则解析为 `oversold`，执行载体为 `sector_selection`

#### Scenario: 显式覆盖

- **WHEN** `regime_mode` 为 `oversold`/`trend`/`bh` 之一
- **THEN** regime SHALL 固定为该值并映射到对应载体，不读取 tech-status

### Requirement: 趋势（动量）选筹

系统 SHALL 在 `select_sectors(mode=trend)` 下按 20 日动量对板块池排序，取 TOP N 等权/归一化权重，且采用只截新入语义（持仓保留）；不要求超跌（`oversold120<0`）。该路径在 `regime_carrier_enabled=false` 且 `regime_mode=trend` 时作为执行选筹，在载体模式下作为展示/备用。

#### Scenario: 动量排序

- **WHEN** 趋势模式选筹
- **THEN** 候选按 20 日动量（`close[d]/close[d-20]-1`）降序，取 TOP N
- **AND** 不设置超跌门槛，不参与贪婪排序

#### Scenario: 与只截新入协同

- **WHEN** 趋势模式且 `hold_until_exit=true`
- **THEN** 已持仓板块保留，新进入候选按动量截断（对齐回测 T-TOP4 只截新入变体）

### Requirement: 模式生效状态输出

系统 SHALL 在 `select_sectors` 结果与 DCA 摘要中输出实际生效的 `regime_mode`（`auto` 解析后的 `oversold`/`trend`/`bh`）、执行载体（`sector_selection`/`fixed_combo`/`broad`）及依据（`trend_up_count`、载体 codes），供前端"牛熊判断 · 科技现状"面板与配置弹窗展示。

#### Scenario: 摘要展示生效模式与载体

- **WHEN** `auto` 模式解析出 `trend` 且 `regime_carrier_enabled=true`
- **THEN** 选筹结果与买入摘要 SHALL 标注 `mode=trend（趋势腿激活 N/9）→ 载体 fixed_combo（588200×0.5+512480×0.5）`

#### Scenario: 载体切换标注

- **WHEN** 相邻两个交易日的执行载体不同（如 oversold→trend）
- **THEN** 切换日买入摘要 SHALL 标注"载体切换：sector_selection → fixed_combo（依据）"
