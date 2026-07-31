## MODIFIED Requirements

### Requirement: DCA 窗口进度追踪
系统 SHALL 在每次 DCA 执行时追踪当前处于窗口的第几天（`schedule_day`），从首次买入日（buy_start）算起（day=0）。buy_start 定义为信号确认日 + exec_delay（默认 1 个交易日），而非信号确认日本身。该信息 SHALL 持久化到 DCA 日志中。

#### Scenario: 信号触发首日
- **WHEN** 贪婪值首次跌破 `pit_greed` 触发信号
- **WHEN** exec_delay=1（默认）
- **THEN** `schedule_day` SHALL 设为 0，日期 SHALL 为信号确认日的下一个交易日
- **THEN** 当日买入金额 SHALL = `dca_weight[0] × trend_factor × max_total`

#### Scenario: 窗口中期执行
- **WHEN** buy_start 后第 3 天执行 DCA
- **THEN** `schedule_day` SHALL 为 2
- **THEN** 当日买入金额 SHALL = `dca_weight[2] × trend_factor × max_total`

#### Scenario: 跳过某天的执行
- **WHEN** 某天因安全制动被跳过
- **THEN** `schedule_day` SHALL NOT 递增（跳过日不计入窗口进度）
- **THEN** 该天的 DCA 权重 SHALL NOT 累积到后续日

### Requirement: DCA 窗口超时兜底
系统 SHALL 为每个指数设置 `dca_fallback` 天数上限。当 `schedule_day` 超过此上限时，系统 SHALL 取消趋势因子压制（trend_factor 强制=1.0），按 DCA 基准权重完成剩余计划内的买入。兜底天数从 buy_start（= 信号确认日 + exec_delay）开始计算。

#### Scenario: 底部长期震荡未确认拐点
- **WHEN** 恒生指数的 `dca_fallback` 为 15 天
- **WHEN** buy_start 后 15 天内 trend 始终为 declining
- **THEN** 第 16 天起 trend_factor SHALL 强制=1.0
- **THEN** 系统 SHALL 在剩余 DCA 计划日内完成买入
