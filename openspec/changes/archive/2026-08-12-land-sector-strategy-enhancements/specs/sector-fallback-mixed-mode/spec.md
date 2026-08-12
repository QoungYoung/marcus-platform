# sector-fallback-mixed-mode Specification

## Purpose

选筹失败回退宽基 + 板块信号恢复后切回板块的混合模式：板块选筹为空（有效信号不足 `min_valid` 或组合为空）时，坑内资金不再跳过，而是回退买入宽基本身 ETF；板块信号恢复后自然切回板块选筹。与回测"坑后选筹失败回退宽基、回退后切回板块"口径一致。

## ADDED Requirements

### Requirement: 选筹失败回退宽基

系统 SHALL 在配置 `fallback_broad=true` 且板块选筹为空（`empty_reason` 非空）时，将 `guide_only` 宽基坑内当日买入金额回退到宽基本身 ETF（如 588000/159915），而非跳过当日买入。

#### Scenario: 有效信号不足回退

- **WHEN** `fallback_broad=true` 且 `select_sectors` 返回 `selected=[]`（含 `min_valid` 不足）
- **THEN** `_build_buy_legs` SHALL 返回宽基本身 ETF 的买入腿（金额=当日坑内金额）
- **AND** 摘要 SHALL 标注"选筹失败回退宽基"及原因

#### Scenario: 配置关闭保持现状

- **WHEN** `fallback_broad=false`
- **THEN** 选筹为空 SHALL 保持现有"跳过当日买入"行为，不购买宽基

### Requirement: 板块信号恢复后切回

系统 SHALL 在板块选筹重新产生有效信号（`selected` 非空）时，自动切回板块 ETF 买入，不再持有宽基回退腿；回退宽基期间的买入不阻塞后续板块买入。

#### Scenario: 信号恢复切回

- **WHEN** 连续若干天选筹为空（回退买宽基），随后某日 `select_sectors` 恢复有效信号
- **THEN** 当日 SHALL 按板块选筹买入，摘要标注"板块信号恢复，切回板块选筹"

#### Scenario: 回退与 DCA 载体共存

- **WHEN** `dca_carrier_enabled=true`（高弹性载体模式）
- **THEN** `fallback_broad` SHALL 仅作用于 `sector_selection` 载体路径，`fixed_combo` 载体不受影响（沿用现有回退 sector_selection 规则）
