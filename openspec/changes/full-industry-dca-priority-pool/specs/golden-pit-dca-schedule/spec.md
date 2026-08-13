# golden-pit-dca-schedule Delta Specification

## ADDED Requirements

### Requirement: DCA 触发源扩展至全行业池
当 `industry_pool_enabled=true` 时，DCA 调度 SHALL 除指数级标的（CHINA_INDICES 等）外，同时处理全行业池中触发信号的行业标的；行业标的复用同一 DCA 窗口/权重/兜底/进度追踪逻辑，窗口标识为 `industry/<id>`。

#### Scenario: 行业触发进入调度
- **WHEN** 某行业触发 DCA 信号且 `industry_pool_enabled=true`
- **THEN** 该行业 SHALL 进入当日 DCA 候选，按 `golden_pit_etf_config` 的 max_total/priority 参与资金池裁决

#### Scenario: 行业 DCA 窗口进度
- **WHEN** 行业信号触发首日
- **THEN** `schedule_day` SHALL 置 0，当日买入金额 SHALL = `dca_weight[0] × max_total × trend_factor`

#### Scenario: 行业调度关闭
- **WHEN** `industry_pool_enabled=false`
- **THEN** 行业标的 SHALL 从 DCA 候选移除，已开启的行业窗口 SHALL 停止新增买入（保留已有持仓与出场逻辑）
