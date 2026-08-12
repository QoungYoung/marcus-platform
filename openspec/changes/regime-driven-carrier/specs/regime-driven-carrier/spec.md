# regime-driven-carrier Specification

## Purpose

环境（regime）→ 载体（carrier）映射决策层：`regime_mode` 解析出的牛熊状态直接决定坑内资金的执行载体（`sector_selection`/`fixed_combo`/`broad`），`_build_buy_legs` 只消费统一解析结果，消除"载体二选一"与"regime 只换选筹风格"的脱节。

## ADDED Requirements

### Requirement: regime 决定执行载体

系统 SHALL 在配置 `regime_carrier_enabled=true` 时，将解析后的 regime 映射为执行载体：`oversold`→`sector_selection`（超跌贪婪选筹）、`trend`→`fixed_combo`（复用 `dca_carrier_<fund>` 高弹性组合）、`bh`→`broad`（宽基躺平）；映射结果 SHALL 作为 DCA 买入腿的唯一来源。

#### Scenario: 超跌环境用动态选筹

- **WHEN** `regime_carrier_enabled=true` 且解析 regime 为 `oversold`
- **THEN** 当日买入腿 SHALL 由 `select_sectors` 动态选筹产生（含 hold_until_exit / fallback 逻辑）
- **AND** 不消费 `dca_carrier_<fund>` 的 fixed_combo 静态腿

#### Scenario: 主升环境用高弹性组合

- **WHEN** `regime_carrier_enabled=true` 且解析 regime 为 `trend`
- **THEN** 当日买入腿 SHALL 按 `dca_carrier_<fund>` 的 `fixed_combo` codes 与权重买入
- **AND** `dca_carrier_<fund>` 未配置 codes 时 SHALL 回退 `broad` 宽基腿

#### Scenario: 宽基躺平

- **WHEN** `regime_carrier_enabled=true` 且解析 regime 为 `bh`
- **THEN** 当日买入腿 SHALL 为宽基本身 ETF（如 588000/159915），不选筹

#### Scenario: 配置关闭保持现状

- **WHEN** `regime_carrier_enabled=false`
- **THEN** 载体 SHALL 保持现有静态优先级（`dca_carrier_enabled` + fixed_combo/broad 优先于 sector_selection），行为与 5.4 一致

### Requirement: 载体解析函数与兜底

系统 SHALL 提供 `resolve_carrier(fund_code, cfg, tech_status) -> {mode, codes, reason}`：auto 按 `trend_up_count >= regime_trend_threshold` 解析，显式 regime 直接映射；tech-status 数据源失败 SHALL 按 `sector_selection` 兜底并记录告警。

#### Scenario: auto 阈值切换载体

- **WHEN** `regime_mode=auto` 且 `trend_up_count >= regime_trend_threshold`
- **THEN** `resolve_carrier` SHALL 返回 `mode=fixed_combo`（含 `dca_carrier_<fund>` codes）
- **AND** 否则 SHALL 返回 `mode=sector_selection`

#### Scenario: 显式 regime 直接映射

- **WHEN** `regime_mode` 为 `oversold`/`trend`/`bh` 之一
- **THEN** `resolve_carrier` SHALL 固定返回对应载体，不读取 tech-status

#### Scenario: 数据源失败兜底

- **WHEN** auto 模式读取 tech-status 抛异常或返回空
- **THEN** `resolve_carrier` SHALL 返回 `mode=sector_selection` 并附兜底原因
