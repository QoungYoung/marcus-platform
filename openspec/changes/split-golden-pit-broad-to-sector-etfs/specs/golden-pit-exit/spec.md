# golden-pit-exit Delta Specification

## MODIFIED Requirements

### Requirement: DCA service executes sell orders on exit signals

The golden pit DCA service SHALL check for exit signals on all currently held ETF positions during each execution cycle, and place sell orders when signals are triggered. For `guide_only` indices (588000/159915), the broad-index exit signal SHALL act as portfolio-level guidance: it SHALL trigger the sell of the corresponding sector ETF portfolio rather than a broad ETF position. Sector ETF positions SHALL additionally exit independently on their own signals.

#### Scenario: Half exit execution

- **WHEN** an index has exit_signal="half_exit" AND the account holds that ETF
- **THEN** the system SHALL place a sell order for 50% of the held shares at limit price × 0.98
- **THEN** the DCA log SHALL record the sell with status "filled" or "failed"

#### Scenario: Full exit execution

- **WHEN** an index has exit_signal="full_exit" or "stop_profit" AND the account holds that ETF
- **THEN** the system SHALL place a sell order for all held shares at limit price × 0.98
- **THEN** the DCA log SHALL record the sell with strategy field set to the exit signal type

#### Scenario: Guide-only 宽基退出驱动板块组合清仓

- **WHEN** 588000（guide_only）触发 full_exit 或 stop_profit
- **THEN** 系统 SHALL 清仓该宽基对应的全部板块 ETF 持仓
- **THEN** 系统 SHALL NOT 对 588000 本身下单（无宽基本仓）

#### Scenario: 板块自身信号独立退出

- **WHEN** 512480 触发 down_turn（连续 3 天回落）
- **THEN** 系统 SHALL 清仓 512480 持仓
- **THEN** 同组合其余板块 ETF 持仓 SHALL NOT 受影响

## ADDED Requirements

### Requirement: 板块 ETF 二次拐点退出配置

系统 SHALL 支持板块 ETF 按 `exit_mode=down_turn` 独立退出：连续 `exit_down_days`（默认 3）天回落时清仓该板块持仓，并受 `exit_fallback_days` 兜底。

#### Scenario: 板块连跌清仓

- **WHEN** 512480 持仓期间连续 3 天回落
- **THEN** 系统 SHALL 清仓 512480 并记录 strategy="down_turn"

#### Scenario: 板块持有兜底

- **WHEN** 板块 ETF 持仓超过 `exit_fallback_days` 且未触发其他退出信号
- **THEN** 系统 SHALL 触发 fallback_exit 清仓该持仓
