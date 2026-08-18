## Purpose

做T账户（account_id='t'）趋势突破短线能力：为 t 账户提供"日频选股入池 -> 盘中实时触发复核 -> 经 t 建仓网关建仓 -> +5%/-8%/-5%/5日 短线出场"的独立于主账户回踩池的短线通道，全部资金与成交只作用于 t 账户，不触碰 stock/golden_pit。

## ADDED Requirements

### Requirement: T账户资金隔离（硬约束）
系统 SHALL 保证趋势突破短线的所有操作只作用于 t 账户：候选入池写入 t 专用候选存储、建仓经 build_t_position（account_id='t'）、平仓经 gateway_execute（account_id='t'）；任何情况下不得读写或动用 stock / golden_pit 账户的资金、持仓或候选池；t 账户可用资金不足时跳过本轮建仓，且不得从其他账户划转资金。

#### Scenario: 仅 t 账户执行
- **WHEN** 趋势突破候选命中并触发建仓
- **THEN** 成交记录 account_id='t'，资金只从 t 账户可用资金扣减，paper_trades/paper_positions 均落在 t 账户

#### Scenario: t 资金不足跳过
- **WHEN** t 账户可用资金不足以按 trend_break 仓位档建仓
- **THEN** 本轮该候选跳过建仓并记录原因，不产生其他账户的资金变动

#### Scenario: 不触碰其他账户
- **WHEN** 趋势突破扫描/监控运行期间
- **THEN** stock 候选池、长期候选池、golden_pit 数据与资金均不被读取用于下单或改写

### Requirement: 独立于回踩池的日频选股入池
系统 SHALL 提供独立的趋势突破选股：按日频数据筛选（当日主力净流入 > 0、5 日主力累计净流入 > 0、市值 < 100 亿、收盘放量突破近 20 日高点、MA20 转上），命中写入 t 专用候选（t_build_scan_results，source='trend_break'）；该通道独立于主账户 candidate_pool 回踩池与 long_term_pool，不依赖其 waiting/回调状态。

#### Scenario: 突破命中入池
- **WHEN** 某标的日频满足全部入池条件
- **THEN** 系统以 source='trend_break' 写入 t 建仓扫描结果，状态 pending，供次日实时复核

#### Scenario: 不依赖回踩池
- **WHEN** 主账户回踩池/长期池无该标的或该标的状态为 expired
- **THEN** 趋势突破通道仍可独立将该标的纳入 t 候选（账户隔离前提下）

### Requirement: 盘中实时触发复核
系统 SHALL 在 t 账户建仓下单前用实时数据复核：当日实时主力净流入 > 0 且量比达到阈值；实时数据源不可用时，不得直接盲买，应降级为次日开盘/低吸复核或跳过。

#### Scenario: 实时确认放行
- **WHEN** 候选标的盘中放量上行且实时主力净流入为正
- **THEN** 允许经 t 建仓网关发起建仓

#### Scenario: 实时数据不可用降级
- **WHEN** 实时主力/量比数据源异常（如代理失败、非交易时段）
- **THEN** 建仓被挂起或按"日频确认 + 次日开盘低吸复核"降级执行，且不放大仓位

### Requirement: 建仓走 t 建仓网关（trend_break 模式）
系统 SHALL 使趋势突破建仓经 t 建仓网关执行，并新增 trend_break 模式：跳过"回踩低吸"时机确认（回踩/量比<2/分时企稳），但保留账户白名单、全局熔断（STOP_ALL/日亏/连续亏损）、时段护栏、涨跌停封板、单笔/单标/总底仓上限、日建仓上限（自动<=3/人工<=5/单票<=1）等硬风控；首次建仓自动放行沿用 ai_led 语义（B1 跳过），但不得豁免其余校验。

#### Scenario: trend_break 放行突破建仓
- **WHEN** 候选标的实时复核通过且未触发任何硬风控
- **THEN** 建仓在 t 账户成交，写入 t_build_events 审计

#### Scenario: 硬风控仍然生效
- **WHEN** t 账户 STOP_ALL、日亏熔断、总底仓超上限或跌停封板任一成立
- **THEN** trend_break 建仓同样被拒绝，不因"突破"语义而豁免

### Requirement: 短线出场条件
系统 SHALL 在 trend_break 建仓次日（T+1 起）为其生成短线出场条件并持续监控：浮盈 +5% 减半、+8% 清仓、浮亏 -5% 硬止损、持有满 5 个交易日强制平仓（超时）；出场执行经 gateway_execute（account_id='t'），受可卖额度（T+1）与熔断约束。

#### Scenario: 止盈清仓
- **WHEN** 持仓浮盈达到 +8%（或 +5% 减半档）
- **THEN** 系统对 t 账户该标的执行相应比例卖出

#### Scenario: 止损
- **WHEN** 持仓浮亏达到 -5%
- **THEN** 系统立即卖出该标的全部持仓（止损动作不被日亏熔断阻断）

#### Scenario: 超时平仓
- **WHEN** 建仓后第 5 个交易日未触发止盈/止损
- **THEN** 系统按市价清仓并审计，避免短线变长线

#### Scenario: T+1 当日不可卖
- **WHEN** 建仓当日（D0）尝试卖出
- **THEN** 卖出被 T+1 规则拦截；持有天数从 D+1 起算

### Requirement: 规模参数（trend_break 独立档）
系统 SHALL 为趋势突破短线提供独立仓位参数档（存 t_build_params，前缀 trend_break_*）：默认单笔 <= 净值 30%、单票 <= 30%、总仓 <= 60%、并行 <= 3 只（以 25 万净值为例即 7.5 万/票）；该档只用于 trend_break 建仓，不改变既有 t 建仓/回转规模语义。

#### Scenario: 按 25 万净值建仓
- **WHEN** t 净值为 25 万且按默认档建仓
- **THEN** 单笔约 7.5 万（30%），总仓不超过 15 万（60%），最多并行 3 只

#### Scenario: 规模档隔离
- **WHEN** 非 trend_break 的既有建仓/回转请求执行
- **THEN** 仍使用既有单笔 4/5/8% 等规模口径，不受 trend_break 档影响

### Requirement: 扫描节流与数据源降级
系统 SHALL 对日频选股扫描做节流（默认每日 <= 50 只、逐只间隔 >= 1 秒），并在日频/实时数据源异常时优雅降级（跳过该轮并告警），不得因扫描故障影响 t 账户既有回转链路。

#### Scenario: 扫描节流
- **WHEN** 触发日频扫描
- **THEN** 扫描标的上限与节流间隔按配置执行，避免打爆数据源

#### Scenario: 数据源异常不连坐
- **WHEN** 日频或实时数据源异常
- **THEN** 本轮扫描/复核跳过并记录，t 账户既有做T回转/止损监控正常运行

### Requirement: 审计
系统 SHALL 为趋势突破短线全链路审计：候选入池（t_build_scan_results）、建仓（t_build_events）、实时复核与出场（t_ai_actions / t_triggers 或专用日志），均标注 account_id='t' 与 source='trend_break'，可通过 t 账户接口查询。

#### Scenario: 审计可查
- **WHEN** 一笔趋势突破建仓成交、被拒或平仓
- **THEN** 生成含账户、来源、价格、数量、原因、校验结果的审计记录，可查询
