## ADDED Requirements

### Requirement: DCA 窗口超时兼容退出信号
当 DCA 窗口超过 `dca_fallback` 上限且累计投入未达到 `max_total_amount` 的计划比例时，系统 SHALL 不触发 `fallback_exit` 退出信号。`fallback_exit` 仅适用于持仓时间超限的情况，与 DCA 建仓超时是两个独立机制。

#### Scenario: DCA 窗口超时不影响持仓退出判断
- **WHEN** 某指数 DCA 窗口已超过 `dca_fallback` 天数
- **WHEN** 该指数尚未完成建仓（total_invested < max_total）
- **WHEN** 该指数已持有的仓位尚未触发退出信号
- **THEN** 系统 SHALL NOT 因 DCA 超时而触发持仓退出
- **THEN** 系统 SHALL 继续按 DCA 兜底逻辑完成剩余买入

#### Scenario: 建仓完成后正常退出逻辑不变
- **WHEN** 某指数已完成建仓（total_invested ≈ max_total）
- **WHEN** 持仓天数超过 `exit_fallback_days` 且未触发其他退出条件
- **THEN** 系统 SHALL 按现有逻辑触发 `fallback_exit`
- **THEN** 退出行为 SHALL NOT 受 DCA 窗口参数影响

### Requirement: 假信号中止后的 DCA 日志清理
当因假信号（greed > entry_greed）中止某指数的 DCA 窗口时，系统 SHALL 清理该窗口的待执行计划并记录中止原因。

#### Scenario: 假信号中止 DCA 窗口
- **WHEN** 指数 greed 突破 `entry_greed` 导致假信号暂停
- **WHEN** DCA 窗口被标记为 `aborted`
- **THEN** 该指数已有的持仓 SHALL 保留（不自动卖出）
- **THEN** DCA 日志 SHALL 记录 `status=aborted`，`strategy` 包含 `fake_signal`
- **THEN** 该窗口的剩余 DCA 计划 SHALL NOT 在后续日自动恢复执行
