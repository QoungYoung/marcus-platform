# bull-regime-selection Specification

## Purpose

牛熊状态驱动的选筹模式切换：基于 `golden-pit/tech-status` 的趋势腿激活数（`trend_up_count`），在超跌（贪婪）选筹、趋势（动量）选筹、宽基躺平三种模式间自动切换，解决"超跌选筹在牛市跑输躺平/趋势"的回测结论在生产未落地的问题。

## ADDED Requirements

### Requirement: 选筹模式配置

系统 SHALL 提供 `regime_mode` 配置（`auto`/`oversold`/`trend`/`bh`）与 `regime_trend_threshold`（默认 5）：
- `oversold`：固定现 greed 超跌选筹（现状默认）
- `trend`：固定 20 日动量 TOP N 选筹（只截新入）
- `bh`：固定买入宽基本身（躺平）
- `auto`：读取科技现状趋势腿激活数，≥ 阈值用 `trend`，否则用 `oversold`

#### Scenario: auto 模式阈值切换

- **WHEN** `regime_mode=auto` 且 `trend_up_count >= regime_trend_threshold`
- **THEN** 选筹 SHALL 使用趋势（动量）模式
- **AND** 否则 SHALL 使用超跌（greed）模式

#### Scenario: 显式覆盖

- **WHEN** `regime_mode` 为 `oversold`/`trend`/`bh` 之一
- **THEN** 选筹 SHALL 固定使用该模式，不读取 tech-status

### Requirement: 趋势（动量）选筹

系统 SHALL 在趋势模式下按 20 日动量对板块池排序，取 TOP N 等权/归一化权重，且采用只截新入语义（持仓保留）；不要求超跌（`oversold120<0`）。

#### Scenario: 动量排序

- **WHEN** 趋势模式选筹
- **THEN** 候选按 20 日动量（`close[d]/close[d-20]-1`）降序，取 TOP N
- **AND** 不设置超跌门槛，不参与贪婪排序

#### Scenario: 与只截新入协同

- **WHEN** 趋势模式且 `hold_until_exit=true`
- **THEN** 已持仓板块保留，新进入候选按动量截断（对齐回测 T-TOP4 只截新入变体）

### Requirement: 模式生效状态输出

系统 SHALL 在 `select_sectors` 结果与 DCA 摘要中输出实际生效的 `regime_mode`（`auto` 解析后的 `oversold`/`trend`/`bh`）及依据（`trend_up_count`），供前端"牛熊判断 · 科技现状"面板与配置弹窗展示。

#### Scenario: 摘要展示生效模式

- **WHEN** `auto` 模式解析出 `trend`
- **THEN** 选筹结果与买入摘要 SHALL 标注 `mode=trend（趋势腿激活 N/9）`
