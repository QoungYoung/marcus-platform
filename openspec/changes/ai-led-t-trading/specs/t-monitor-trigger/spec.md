## MODIFIED Requirements

### Requirement: Worker 主动唤醒 Agent
系统 SHALL 在触发命中后由 Worker 主动调用 DSH bridge 的 /chat 接口唤醒做T Agent（附触发上下文），Agent 不轮询 t_triggers；桥不可达时降级为低频轮询兜底；Worker 永不直接调用下单接口。唤醒语义 SHALL 为"AI 决策"而非"AI 复核"：AI 被唤醒后自主看盘（行情/持仓/指标）并决定执行/等待/放弃/调整条件，而非仅做 auto/human 二选一。

#### Scenario: 命中即唤醒
- **WHEN** TMonitor 写入 pending 事件
- **THEN** Worker 主动 POST /chat 携带触发上下文唤醒做T Agent 决策（上下文含触发快照、当前持仓、该条件最近 N 次决策与连续命中计数）

#### Scenario: AI 决策而非复核
- **WHEN** AI 会话被触发事件唤醒
- **THEN** 会话以决策主体视角运行：可调用行情/持仓/指标工具，输出执行/等待/放弃/调整条件之一，且决策写入审计（t_ai_actions）

#### Scenario: 桥不可达降级
- **WHEN** /chat 桥连续不可达
- **THEN** 降级为低频轮询（如 30s）兜底消费 pending 事件，保证不丢触发；兜底处理仅标记事件待 AI 下次唤醒，不自动下单

### Requirement: 触发事件表与状态机
系统 SHALL 用 t_conditions（条件注册表，条件元组含触发价/复归价/量比阈值/企稳确认/卖出目标/止损/时间止损/regime_gate）与 t_triggers（事件流）持久化做T条件与触发事件；t_triggers 状态机为 `pending → (auto_ready | human_confirm) → executed | blocked | cancelled`，用 `UPDATE ... WHERE status='pending' RETURNING` 原子消费防重复处理，human_confirm 超时自动 cancelled。AI 主导模式下 t_triggers 增加 `ai_decided` 中间态：pending → ai_decided（AI 已决策）→ executed | blocked | cancelled | await_retry。

#### Scenario: 条件注册
- **WHEN** 选股 Agent 为实盘池标的生成做T条件
- **THEN** t_conditions 写入条件元组（含当日 trade_date 维度、regime_gate、发布者 session_id 字段），仅允许 account_id='t' 且已有底仓的标的

#### Scenario: 命中写事件
- **WHEN** TMonitor 判定触发命中（已过 regime_gate + 复合确认）
- **THEN** t_triggers 写入 status='pending' 事件，快照含触发价/命中报价/建议买卖价/滑点预算/置信度/连续命中计数

#### Scenario: AI 决策后状态流转
- **WHEN** AI 对 pending 事件做出决策（执行/等待/放弃/调整条件）
- **THEN** 事件流转为 ai_decided 并记录决策；执行走网关（executed/blocked），放弃为 cancelled，等待保留为 await_retry（冷却后重新武装）

#### Scenario: 原子消费防重复
- **WHEN** 多个消费者尝试处理同一 pending 事件
- **THEN** 仅一个消费者通过 `UPDATE ... WHERE status='pending' RETURNING` 成功获得处理权，其余失败

#### Scenario: 人工确认超时
- **WHEN** 事件进入 human_confirm 且超过确认时限（如 2 分钟）未处理
- **THEN** 事件自动置为 cancelled，避免悬空
