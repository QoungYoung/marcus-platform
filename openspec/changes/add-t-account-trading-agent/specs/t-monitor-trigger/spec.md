## Purpose

做T监控与触发：TMonitor 分层采样与复合企稳确认，盘中量比时段归一，滞回/去抖/armed 状态机，t_conditions/t_triggers 表与状态机，Worker 命中后主动唤醒做T Agent（不轮询、不直接下单）。

## ADDED Requirements

### Requirement: 做T监控器分层采样
系统 SHALL 提供做T监控器 TMonitor（后端 Worker daemon 线程，30s 周期），对核心底仓（≤10-20 只）用腾讯 qt 实时行情直连（use_cache=False）采样、观察池用 30s-1min 缓存采样；非交易时段硬跳过不轮询；多标的并发取价限 ≤5 线程，轮询节奏加 ±jitter 并错开既有监控器首轮（初始偏移）。

#### Scenario: 监控器注册与启动
- **WHEN** Worker 启动
- **THEN** TMonitor 注册进 worker_main，状态可被 /api/v1/scheduler 快照与 start/stop 命令控制

#### Scenario: 非交易时段不轮询
- **WHEN** 当前为非交易日、午休或开盘前
- **THEN** TMonitor 跳过本轮采样，不发起行情请求

#### Scenario: 核心标的直连取价
- **WHEN** 对做T实盘池核心标的（≤10-20 只）取实时价
- **THEN** 使用腾讯 qt 接口且绕过 5 分钟缓存（use_cache=False），单轮延迟可承载 30s 周期

### Requirement: 盘中量比时段归一
系统 SHALL 用时段归一公式计算盘中量比：`盘中量比(t) = [当期累计换手 × (240 / 已开盘连续分钟)] / 近N日"同一时刻"累计换手率均值`，修正现有 `turnover_rate/2.0` 固定除法的早盘/高开误报；基准（benchmark_turnover_profile / intraday_volume_profile）用分钟线历史（腾讯 m5 6日/新浪 300根/brze stk_mins）构造，无需自积累。

#### Scenario: 量比归一计算
- **WHEN** 盘中计算某标的量比
- **THEN** 按"当前累计换手×时段伸缩/近N日同刻均值"计算，早盘天然高累计换手不再被误判为放量

#### Scenario: 量比基准可获取
- **WHEN** 需要量比基准数据
- **THEN** 可从腾讯 ifzq m5 历史、新浪分钟线或 brze stk_mins 任一可用源获取近 N 日同刻均值

### Requirement: 复合企稳确认触发
系统 SHALL 用复合确认（而非单一"放量下跌到XX元"字符串）判断做T触发：价格到位（支撑位）∧ 量能企稳（量比归一达标）∧ 分时企稳（不再创新低/下影线/量能萎缩回升，用 1min/5min 线判断）∧ 当日波动结构允许；触发价锚定支撑位（均线/前低/筹码峰/整数关）而非任意价位。

#### Scenario: 复合确认命中触发
- **WHEN** 某标的价格到达支撑位且量能企稳且分时企稳且波动结构允许
- **THEN** TMonitor 判定触发命中，进入事件生成流程

#### Scenario: 单一条件不触发
- **WHEN** 仅价格到位但量能或分时未企稳
- **THEN** 不判定触发命中（避免左侧逆势接飞刀）

### Requirement: 滞回去抖与 armed 状态机
系统 SHALL 对价格触发加滞回带（触发价与复归价两档，触发后须回归复归价才重新武装）、cooldown 去抖（同标的同条件冷却期内不重复产生事件）、条件行状态机（armed_at/last_triggered_at/trigger_count_today），并保证价源一致性（判断用同一行情源同轮数据）。

#### Scenario: 触发后去抖
- **WHEN** 某条件已触发且处于冷却期或未重新武装
- **THEN** 同一条件不再产生新触发事件

#### Scenario: 复归后重新武装
- **WHEN** 价格从触发价回归到复归价（≥触发价×(1+0.3%~0.5%)）
- **THEN** 条件重新武装，后续再次穿越可再次触发

### Requirement: 触发事件表与状态机
系统 SHALL 用 t_conditions（条件注册表，条件元组含触发价/复归价/量比阈值/企稳确认/卖出目标/止损/时间止损/regime_gate）与 t_triggers（事件流）持久化做T条件与触发事件；t_triggers 状态机为 `pending → (auto_ready | human_confirm) → executed | blocked | cancelled`，用 `UPDATE ... WHERE status='pending' RETURNING` 原子消费防重复处理，human_confirm 超时自动 cancelled。

#### Scenario: 条件注册
- **WHEN** 选股 Agent 为实盘池标的生成做T条件
- **THEN** t_conditions 写入条件元组（含当日 trade_date 维度、regime_gate 字段），仅允许 account_id='t' 且已有底仓的标的

#### Scenario: 命中写事件
- **WHEN** TMonitor 判定触发命中（已过 regime_gate + 复合确认）
- **THEN** t_triggers 写入 status='pending' 事件，快照含触发价/命中报价/建议买卖价/滑点预算/置信度

#### Scenario: 原子消费防重复
- **WHEN** 多个消费者尝试处理同一 pending 事件
- **THEN** 仅一个消费者通过 `UPDATE ... WHERE status='pending' RETURNING` 成功获得处理权，其余失败

#### Scenario: 人工确认超时
- **WHEN** 事件进入 human_confirm 且超过确认时限（如 2 分钟）未处理
- **THEN** 事件自动置为 cancelled，避免悬空

### Requirement: Worker 主动唤醒 Agent
系统 SHALL 在触发命中后由 Worker 主动调用 DSH bridge 的 /chat 接口唤醒做T Agent（附触发上下文），Agent 不轮询 t_triggers；桥不可达时降级为低频轮询兜底；Worker 永不直接调用下单接口。

#### Scenario: 命中即唤醒
- **WHEN** TMonitor 写入 pending 事件
- **THEN** Worker 主动 POST /chat 携带触发上下文唤醒做T Agent 复核

#### Scenario: 桥不可达降级
- **WHEN** /chat 桥连续不可达
- **THEN** 降级为低频轮询（如 30s）兜底消费 pending 事件，保证不丢触发
