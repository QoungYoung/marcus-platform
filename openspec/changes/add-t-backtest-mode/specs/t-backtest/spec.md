## Purpose

做T监控条件（自由表达式/阈值）的历史回测验证能力：用历史分钟数据回放做T触发-复核-执行链路，量化监控条件在历史上的触发质量与收益表现，为条件参数调优提供数据依据。

## ADDED Requirements

### Requirement: 回测任务生命周期
系统 SHALL 支持创建、运行、查询、取消和删除做T回测任务。任务参数 MUST 包含：标的（symbol）、起止日期、监控条件（condition_id 列表或条件定义）、初始底仓假设（股数与成本价）、回放粒度（默认 m5）。任务状态 MUST 覆盖 pending/running/completed/failed/cancelled。同一任务并发运行 MUST 被拒绝。

#### Scenario: 创建并运行单标的多日回测
- **WHEN** 用户创建回测任务（symbol=SH600519，日期范围=近 30 个交易日，条件=某 low_buy 表达式，初始底仓=1000 股）
- **THEN** 任务进入 pending，可被启动；启动后进入 running，逐日逐 tick 回放；完成后状态为 completed 且结果可查询

#### Scenario: 重复启动被拒绝
- **WHEN** 已 running 的回测任务再次被启动
- **THEN** 系统拒绝并返回明确错误，不产生第二个执行流

### Requirement: 历史数据预取与前视防护
系统 SHALL 在回测前预取标的 m5 分钟线、指数（沪深300/上证/深成指）行情与标的日线基准，并落本地缓存。任何回测评估点 SHALL 只使用时间戳早于或等于该评估点的数据，MUST NOT 引用评估点之后的数据（禁止前视）。数据缺失或不可用 SHALL 记为缺口并跳过对应 tick，不得伪造数据。

#### Scenario: 回测评估点不引用未来数据
- **WHEN** 回放引擎评估第 T 日 10:30 的 m5 bar
- **THEN** 字段快照只由 ≤ 10:30 的 bar 与 ≤ T-1 日的日线计算，绝不包含 10:30 之后的价格、成交量或指标

#### Scenario: 数据缺口跳过
- **WHEN** 某交易日某标的 m5 数据缺失（如停牌）
- **THEN** 该日该标的的评估被跳过并在结果中标记数据缺口，不影响其他交易日回放

### Requirement: m5 回放触发
回放引擎 SHALL 按 m5 tick 推进，对每个 tick 重建与实盘 TMonitor 同构的字段快照（quote/minute/tech/regime/position），并复用做T表达式求值与通用护栏（regime 闸门、14:45 时段、armed 状态、冷却）判定是否触发。触发判定的时间源 MUST 为回测时间而非系统当前时间。触发命中 SHALL 写入回测触发事件，快照 SHALL 记录该 tick 的全部字段值。

#### Scenario: 表达式触发与护栏拦截
- **WHEN** 历史某 tick 满足条件表达式（如 quote.current ≤ 触发价 ∧ vol_ratio ≥ 1.5 ∧ regime.state ∈ [ACTIVE, CAUTIOUS]）
- **THEN** 系统写入一条回测触发事件，携带该 tick 的字段快照与触发时间
- **WHEN** 同 tick 的 regime 闸门为 BLOCKED 或时间 ≥ 14:45 或条件未 armed
- **THEN** 不写入触发事件，且拦截原因被记录

### Requirement: 回测账本与撮合
系统 SHALL 维护回测账本：每标的的底仓股数、成本价、可卖量、当日回转量、已实现盈亏与日账本（回转额/买卖计数）。撮合 SHALL 复刻实盘网关校验规则（可卖底仓、跌停/涨停禁单、分档买腿上限、日亏损熔断、回转额上限、卖出在途锁），成交价 SHALL 为触发后下一根 m5 bar 的 close 价 ± 滑点（默认 0.1%，可配置），T+0 闭环（高抛减仓→低吸买回）SHALL 被显式建模，收盘时未回补的底仓 SHALL 结转次日。

#### Scenario: 无底仓标的低吸被拒
- **WHEN** 回测中某标的无可卖底仓（初始无底仓且当日未买入）时低吸触发进入撮合
- **THEN** 撮合拒绝该笔并记录"无底仓"拦截原因，账本不变

#### Scenario: 当日回转闭环
- **WHEN** 某标的底仓 1000 股，盘中高抛 300 股（成交）后低吸 300 股（成交）
- **THEN** 当日回转量累计 600 股，收盘底仓恢复 1000 股，已实现盈亏 = 高抛与低吸价差 × 300 股 − 双边费用与滑点

### Requirement: 真实 LLM 复核与沙盒隔离
每个回测任务 SHALL 使用专用 Agent 会话（回测沙盒）执行触发复核：复核决策（auto/human）由真实 LLM 作出，但该会话的全部写工具 SHALL 只作用于回测账本，MUST NOT 触达生产订单表、生产持仓或任何真实交易通道。每次复核 SHALL 落库：触发上下文、LLM 决策、理由与耗时。LLM 决策为 human 的触发 SHALL 记为"升级不成交"并保留记录，不进入撮合。

#### Scenario: 回测复核不产生真实订单
- **WHEN** 回测触发事件进入 LLM 复核且 LLM 决策 auto，随后撮合成交
- **THEN** 成交只写入回测账本与回测结果表，生产 paper_orders 表与生产账户 SHALL 无任何变化

#### Scenario: 升级人工不成交
- **WHEN** LLM 复核决策为 human（或 classify_escalation 判定升级）
- **THEN** 该触发记为"升级不成交"，事件流保留决策与理由，不进入撮合

### Requirement: 回测结果与报告
系统 SHALL 将回测全事件流（触发/复核/成交/拦截/数据缺口）落库，并生成指标报告：触发次数、成交率、Agent 拦截率、单笔盈亏分布、胜率、日内闭环率、底仓成本漂移、最大回撤、总收益，以及与"买入持有"基准的对比。报告 MUST 显式标注回测与实盘的口径差异：回放粒度（m5 vs 30s 轮询）、量比口径（分钟量均值 vs 换手率）、regime L1 近似、成交假设（下一根 close ± 滑点）、固定初始底仓。

#### Scenario: 报告包含口径差异声明
- **WHEN** 回测任务完成生成报告
- **THEN** 报告包含完整指标与逐条口径差异说明，任何依赖近似口径的指标均有对应标注

### Requirement: 回测入口
系统 SHALL 提供 REST API（任务创建/启动/取消/查询/报告）与 DSH Agent 工具 `run_t_backtest`（Agent 在对话中发起回测并获取结果摘要）。Agent 工具 SHALL 在回测运行中返回任务 id 与进度状态，完成后返回结果摘要与报告入口。

#### Scenario: Agent 发起回测
- **WHEN** Agent 调用 `run_t_backtest`（标的、日期范围、条件、底仓）
- **THEN** 任务被创建并异步运行，工具返回任务 id 与状态；完成后可再次调用获取指标摘要
