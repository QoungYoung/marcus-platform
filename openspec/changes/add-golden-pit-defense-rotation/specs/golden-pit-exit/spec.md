## MODIFIED Requirements

### Requirement: DCA service executes sell orders on exit signals
The golden pit DCA service SHALL check for exit signals on all currently held ETF positions during each execution cycle, and place sell orders when signals are triggered. After a full exit or stop-profit sell, the service SHALL rotate the freed capital into the defense portfolio (红利/银行/黄金/国债/有色 equal weight) as the default post-exit allocation instead of keeping the capital idle.

#### Scenario: Half exit execution
- **WHEN** an index has exit_signal="half_exit" AND the account holds that ETF
- **THEN** the system SHALL place a sell order for 50% of the held shares at limit price × 0.98
- **THEN** the DCA log SHALL record the sell with status "filled" or "failed"

#### Scenario: Full exit execution
- **WHEN** an index has exit_signal="full_exit" or "stop_profit" AND the account holds that ETF
- **THEN** the system SHALL place a sell order for all held shares at limit price × 0.98
- **THEN** the DCA log SHALL record the sell with strategy field set to the exit signal type

#### Scenario: Full exit rotates freed capital into defense portfolio
- **WHEN** an index has exit_signal="full_exit" or "stop_profit" AND the position is sold
- **THEN** the freed capital SHALL be allocated into the defense portfolio (红利 510880 / 银行 512800 / 黄金 518880 / 国债 511010 / 有色 512400) with equal weights
- **THEN** the DCA log SHALL record the defense allocation with strategy containing "defense_rotation"

#### Scenario: Defense takeover ends on next growth index entry
- **WHEN** the defense portfolio is active AND any growth index enters golden pit or warning status
- **THEN** the system SHALL stop new defense allocation and resume DCA buying for the growth index

## ADDED Requirements

### Requirement: 防御组合承接撤场资金
系统 SHALL 在成长指数撤场（full_exit/stop_profit/fallback_exit）后将可用资金按防御组合五标的（红利/银行/黄金/国债/有色）等权配置；防御组合的入坑/撤场按防御监测的独立价格分位阈值执行。

#### Scenario: 撤场后资金进入防御组合
- **WHEN** 某成长指数触发 full_exit/stop_profit/fallback_exit
- **THEN** 可用资金 SHALL 按 20% 等权分配至红利/银行/黄金/国债/有色
- **THEN** 分配行为 SHALL 记录在 DCA 日志（strategy 包含 defense_rotation）

#### Scenario: 防御标的自身撤场后重新平衡
- **WHEN** 防御标的（如红利 510880）价格分位 ≥ 其撤场阈值 P40
- **THEN** 该防御标的部分 SHALL 触发 full_exit 信号
- **THEN** 该部分资金 SHALL 重新平衡至其余防御标的（银行/黄金/国债/有色）

#### Scenario: 成长指数重新入坑时转回成长仓
- **WHEN** 任一成长指数进入 golden_pit 或 warning 状态
- **THEN** 防御组合承接 SHALL 停止
- **THEN** 资金 SHALL 按坑内仓位配置（指数 90% + 588200 5% + 512480 5%）开始新一轮定投