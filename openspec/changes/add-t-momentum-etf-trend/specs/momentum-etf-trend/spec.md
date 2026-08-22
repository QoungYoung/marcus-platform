# Spec: t 账户科技 ETF 动量趋势（momentum-etf-trend）

## ADDED Requirements

### Requirement: 科技ETF候选池

系统 SHALL 维护 tech7 科技 ETF 候选池（创业板50/半导体/人工智能/5G通信/大数据/通信设备/科创芯片），其日线数据 SHALL 持续落库；数据缺失标的 SHALL 在信号计算中跳过。

#### Scenario: 池内标的数据不足
- **WHEN** 某标的连续交易日数据少于 21 根
- **THEN** 该标的 SHALL 不参与动量信号计算

#### Scenario: 池内标的数据齐全
- **WHEN** tech7 全部标的数据完整
- **THEN** 系统 SHALL 计算全部 7 只标的的动量并参与排序

- **REQ-MOM-001** 系统维护一个固定的科技 ETF 候选池（tech7）：创业板50、半导体、人工智能、5G通信、大数据、通信设备、科创芯片，对应 arkvol tech-hardware-greed 贪婪数据覆盖的场内 ETF。
- **REQ-MOM-002** 候选池标的的日线数据（前复权 OHLCV）持续落库，作为动量与均线计算的输入；数据缺失或不足的标的在信号计算中跳过。

### Requirement: 动量信号

系统 SHALL 在每交易日收盘后计算池内每只标的的 20 日动量（当日收盘 / 20 个交易日前收盘 - 1）并降序排序，目标组合 SHALL 为动量最高的 3 只等权；有效标的不足时按实际数量选取，不足 1 只时空仓。

#### Scenario: 正常排序选 TOP3
- **WHEN** 7 只标的动量均可计算
- **THEN** 系统 SHALL 选取动量最高的 3 只构成等权目标组合

#### Scenario: 有效标的不足
- **WHEN** 仅 2 只标的有有效动量
- **THEN** 目标组合 SHALL 为这 2 只等权

#### Scenario: 全部无有效动量
- **WHEN** 所有标的动量均不可计算
- **THEN** 目标组合 SHALL 为空（空仓）

- **REQ-MOM-003** 每个交易日收盘后，对池内每个标的计算 20 日动量（当日收盘 / 20 个交易日前收盘 - 1），并按动量降序排序。
- **REQ-MOM-004** 系统从排序结果中选取动量最高的 3 只标的构成目标组合（等权）。若有效动量标的不足 3 只，按实际数量选取；不足 1 只时空仓。

### Requirement: 贪婪门控（arkvol）

系统 SHALL 用各标的 250 日贪婪分位（截至当日的贪婪历史分位）做过热过滤：分位大于 0.9 的标的 SHALL 从目标组合剔除；全部被剔除时 SHALL 空仓；贪婪数据缺失时该标的不做门控，数据源整体不可用时门控 SHALL 自动关闭并告警。

#### Scenario: 过热剔除
- **WHEN** 某标的贪婪分位 = 0.95
- **THEN** 该标的 SHALL 被排除出目标组合

#### Scenario: 全部过热空仓
- **WHEN** 动量 TOP3 的贪婪分位均 > 0.9
- **THEN** 目标组合 SHALL 为空，系统 SHALL 空仓等待

#### Scenario: 贪婪数据缺失降级
- **WHEN** arkvol 贪婪数据不可用（如 2025-01 之前的窗口或接口失败）
- **THEN** 系统 SHALL 不做门控并按无门控运行，且 SHALL 记录告警日志

- **REQ-MOM-005** 每个标的使用其 250 日贪婪分位（当前值在截至当日的历史贪婪序列中的分位）作为过热过滤器；分位大于 0.9 的标的从目标组合中剔除。
- **REQ-MOM-006** 若全部候选标的均被贪婪门控剔除，目标组合为空（空仓等待）。
- **REQ-MOM-007** 贪婪历史数据缺失（如 2025-01 之前的回测窗口或数据源不可用）时，该标的不做门控（视为未过热），数据源整体不可用时门控功能自动关闭并记录告警。

### Requirement: 轮动与调仓

系统 SHALL 每 10 个交易日执行一次调仓：卖出不在目标组合的持仓、买入目标组合中未持有的标的；非调仓日 SHALL 不主动调仓（无独立止损/止盈）；调仓 SHALL 遵守 t 账户时段/封板/规模/T+1 护栏，被拦截时次日重试。

#### Scenario: 双周调仓换出弱动量
- **WHEN** 距上次调仓已满 10 个交易日且某持仓跌出动量 TOP3
- **THEN** 系统 SHALL 卖出该持仓并买入新进入 TOP3 的标的

#### Scenario: 非调仓日不动作
- **WHEN** 距上次调仓不足 10 个交易日
- **THEN** 系统 SHALL 保持持仓不动（即使浮亏超过 -5%）

#### Scenario: 护栏拦截重试
- **WHEN** 调仓尝试被涨跌停封板或时段护栏拦截
- **THEN** 该调仓 SHALL 在下一交易日自动重试

- **REQ-MOM-008** 系统每 10 个交易日（双周）执行一次调仓：按最新目标组合调整持仓，卖出不在目标组合中的标的、买入目标组合中未持有的标的。
- **REQ-MOM-009** 调仓日之外的交易日不主动调整仓位（允许涨跌波动，不做独立止损/止盈）。
- **REQ-MOM-010** 调仓执行遵循 t 账户既有时段护栏（自动建仓窗口 9:45-13:00）、涨跌停封板禁单、规模上限（单笔≤净值30%、单标≤30%、总仓≤60%、最多 3 只）与 T+1 规则；被护栏拦截的调仓在下一交易日重试。

### Requirement: 账户隔离与审计

动量趋势交易 SHALL 只作用于 account_id='t'，不触碰 stock/golden_pit；候选与事件 SHALL 分别写入 t_build_scan_results(source='mom_etf') 与 t_build_events（reason 含 mom_etf）；与 V反 信号并行且仓位上限共享。

#### Scenario: t 账户隔离
- **WHEN** 动量趋势模块执行建仓
- **THEN** 成交 SHALL 只发生在 account_id='t'，stock/golden_pit 账户 SHALL 无任何变更

#### Scenario: 候选与事件可审计
- **WHEN** 一次扫描与一次调仓完成
- **THEN** t_build_scan_results 中 SHALL 存在 source='mom_etf' 的候选记录，t_build_events 中 SHALL 存在 reason 含 mom_etf 的建仓/平仓事件

- **REQ-MOM-011** 动量趋势交易只作用于 account_id='t'，不触碰 stock/golden_pit 账户资金与持仓；t 资金不足时跳过并记录。
- **REQ-MOM-012** 扫描候选与建仓/平仓事件分别写入 t_build_scan_results(source='mom_etf') 与 t_build_events（reason 含 mom_etf 标记），可用于页面展示与复盘。
- **REQ-MOM-013** 与现有 V反 信号（vrebounce/vreb_etf）并行运行且互不干扰；仓位上限为 t 账户整体共享。

### Requirement: 灰度与监控

模块 SHALL 默认关闭，T_MOM_ETF_ENABLED=1 启用；状态 SHALL 可通过 API 查询（启用/运行/上次扫描/最近候选）；贪婪数据连续失败超过阈值时 SHALL 停止自动调仓。

#### Scenario: 灰度关闭
- **WHEN** T_MOM_ETF_ENABLED=0
- **THEN** 模块 SHALL 仅登记不运行，API 状态返回 enabled=false

#### Scenario: 贪婪数据连续失败停机
- **WHEN** arkvol 贪婪拉取连续失败超过配置阈值
- **THEN** 系统 SHALL 停止自动调仓并输出告警

- **REQ-MOM-014** 模块默认关闭，环境变量 T_MOM_ETF_ENABLED=1 时启用；监控状态可通过 API 查询（启用/运行/上次扫描/最近候选）。
- **REQ-MOM-015** 贪婪数据拉取失败时降级为无门控运行并输出告警日志；连续失败超过阈值时停止该模块的自动调仓（防止门控失效裸奔）。