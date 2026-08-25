## MODIFIED Requirements

### Requirement: 轮动与调仓

系统 SHALL 每 10 个交易日执行一次调仓：卖出不在目标组合的持仓、买入目标组合中未持有的标的；非调仓日 SHALL 不主动调仓（无独立止损/止盈）；调仓 SHALL 遵守 t 账户时段/封板/规模/T+1 护栏，被拦截时次日重试。调仓节律 SHALL 以 mom_etf 最近一次已成交（executed）的调仓记录日期为基准计算（审计原因字段被更新覆盖不影响节律）。mom_etf 建仓 SHALL 不受「总底仓 ≤ 净值 60%」上限约束（单笔/单标上限保留），其余护栏（时段/封板/T+1）保留。换出 SHALL 只针对 mom_etf 目标组合内已执行候选标记的持仓（不卖出其他流程在 t 账户建的仓位）；买入 SHALL 跳过 t 账户实际可卖账本中已持有的标的（含其他流程建仓，避免重复买入）。

#### Scenario: 双周调仓换出弱动量
- **WHEN** 距上次调仓已满 10 个交易日且 mom_etf 自有持仓跌出动量 TOP3
- **THEN** 系统 SHALL 卖出该持仓并买入新进入 TOP3 的标的

#### Scenario: 非调仓日不动作
- **WHEN** 距上次调仓不足 10 个交易日
- **THEN** 系统 SHALL 保持持仓不动（即使浮亏超过 -5%）

#### Scenario: 护栏拦截重试
- **WHEN** 调仓尝试被涨跌停封板或时段护栏拦截
- **THEN** 该调仓 SHALL 在下一交易日自动重试

#### Scenario: 底仓已超 60% 仍可调仓
- **WHEN** t 账户底仓市值已达净值 60% 以上且目标组合出现未持有标的
- **THEN** 系统 SHALL 仍按单笔/单标规模上限执行建仓，总底仓上限 SHALL 不拦截

#### Scenario: 调仓节律按成交记录计
- **WHEN** mom_etf 调仓发生成交且审计原因被后续更新覆盖（不再含 mom_etf 字样）
- **THEN** 系统 SHALL 仍以该次成交对应的 mom_etf 调仓记录日期作为「上次调仓日期」计算双周节律

#### Scenario: 不卖出其他流程持仓
- **WHEN** t 账户存在由 V反/每日自动选股等其他流程建仓、且不在 mom_etf 已执行候选标记内的持仓
- **THEN** mom_etf 调仓 SHALL 不卖出该持仓（并行互不干扰），买入时仍视为已持有而跳过

- **REQ-MOM-008** 系统每 10 个交易日（双周）执行一次调仓：按最新目标组合调整持仓，卖出不在目标组合中的 mom_etf 自有持仓（已执行候选标记）、买入目标组合中未持有的标的；「距上次调仓」以 mom_etf 最近一次已成交（executed）的调仓记录日期计算，与审计原因字段内容无关（原因字段被更新覆盖不影响节律）。
- **REQ-MOM-009** 调仓日之外的交易日不主动调整仓位（允许涨跌波动，不做独立止损/止盈）。
- **REQ-MOM-010** 调仓执行遵循 t 账户既有时段护栏（自动建仓窗口 9:45-13:00）、涨跌停封板禁单、规模上限（单笔≤净值 30%、单标≤30%）与 T+1 规则；mom_etf 建仓 SHALL 不受总底仓≤净值 60% 上限约束；被时段/封板护栏拦截的调仓在下一交易日重试；换出仅针对 mom_etf 已执行候选标记的持仓，买入跳过实际账本已持有标的。

### Requirement: 账户隔离与审计

动量趋势交易 SHALL 只作用于 account_id='t'，不触碰 stock/golden_pit；候选与事件 SHALL 分别写入 t_build_scan_results(source='mom_etf') 与 t_build_events；「已持有」判断 SHALL 基于 t 账户实际可卖持仓账本（含其他流程建仓的标的），而非仅 mom_etf 自有事件；与 V反 信号并行且互不干扰。

#### Scenario: t 账户隔离
- **WHEN** 动量趋势模块执行建仓
- **THEN** 成交 SHALL 只发生在 account_id='t'，stock/golden_pit 账户 SHALL 无任何变更

#### Scenario: 候选与事件可审计
- **WHEN** 一次扫描与一次调仓完成
- **THEN** t_build_scan_results 中 SHALL 存在 source='mom_etf' 的候选记录，t_build_events 中 SHALL 存在本次建仓/平仓事件

#### Scenario: 已持有标的识别
- **WHEN** 目标组合中的标的已由其他流程（如每日自动选股）建仓持有
- **THEN** 系统 SHALL 将其视为已持有：不重复买入，且调仓换出判断 SHALL 覆盖该持仓

- **REQ-MOM-011** 动量趋势交易只作用于 account_id='t'，不触碰 stock/golden_pit 账户资金与持仓；t 资金不足时跳过并记录。
- **REQ-MOM-012** 扫描候选与建仓/平仓事件分别写入 t_build_scan_results(source='mom_etf') 与 t_build_events，可用于页面展示与复盘。
- **REQ-MOM-013** 与现有 V反 信号（vrebounce/vreb_etf）并行运行且互不干扰；「已持有」判断基于 t 账户实际可卖持仓账本，覆盖全部建仓来源的标的。
