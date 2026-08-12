# sector-fallback-mixed-mode Specification

## Purpose

选筹失败回退链 + 板块信号恢复后切回板块的混合模式：板块选筹为空（有效信号不足 `min_valid` 或组合为空）时，坑内资金按三级链回退——先 `fixed_combo` 静态高弹性组合，再宽基本身 ETF；板块信号恢复后自然切回板块选筹。与回测"坑后选筹失败回退宽基、回退后切回板块"口径一致。

## MODIFIED Requirements

### Requirement: 选筹失败三级回退

系统 SHALL 在配置 `fallback_broad=true` 且板块选筹为空（`empty_reason` 非空）时，将 `guide_only` 宽基坑内当日买入金额按三级链回退：第一级 `fixed_combo`（复用 `dca_carrier_<fund>` codes，若已配置）；第二级宽基本身 ETF（如 588000/159915）；两级均不可用时跳过当日买入。摘要 SHALL 标注回退层级与原因。

#### Scenario: 选筹空回退高弹性组合

- **WHEN** `fallback_broad=true` 且 `select_sectors` 返回 `selected=[]` 且 `dca_carrier_<fund>` 配置了 fixed_combo codes
- **THEN** `_build_buy_legs` SHALL 按 codes/权重返回 fixed_combo 买入腿（金额=当日坑内金额）
- **AND** 摘要 SHALL 标注"选筹失败回退 fixed_combo"及原因

#### Scenario: 无高弹性组合回退宽基

- **WHEN** `fallback_broad=true` 且选筹为空 且 fixed_combo codes 缺失
- **THEN** `_build_buy_legs` SHALL 返回宽基本身 ETF 买入腿（金额=当日坑内金额）
- **AND** 摘要 SHALL 标注"选筹失败回退宽基"及原因

#### Scenario: 配置关闭保持现状

- **WHEN** `fallback_broad=false`
- **THEN** 选筹为空 SHALL 保持现有"跳过当日买入"行为，不购买任何回退腿

### Requirement: 板块信号恢复后切回

系统 SHALL 在板块选筹重新产生有效信号（`selected` 非空）时，自动切回板块 ETF 买入，不再持有回退腿；回退期间的买入不阻塞后续板块买入。

#### Scenario: 信号恢复切回

- **WHEN** 连续若干天选筹为空（回退 fixed_combo/broad），随后某日 `select_sectors` 恢复有效信号
- **THEN** 当日 SHALL 按板块选筹买入，摘要标注"板块信号恢复，切回板块选筹"

#### Scenario: 回退与 regime 载体共存

- **WHEN** `regime_carrier_enabled=true` 且解析载体为 `sector_selection`
- **THEN** 三级回退 SHALL 仅作用于该 `sector_selection` 载体路径
- **AND** 解析载体为 `fixed_combo`/`broad` 时直接按对应载体买入，不触发回退链
