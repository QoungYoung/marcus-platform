## Purpose

做T专用账户（t_account）与做T标的选股：独立资金/独立风控参数，三层池流转（底仓候选池/做T实盘池/观察池）与可T质量打分制选股，禁止无底仓建仓式做T。

## ADDED Requirements

### Requirement: 做T专用账户注册与资金隔离
系统 SHALL 提供做T专用账户 `t_account`，注册进 `paper_accounts` 注册表，具备独立初始资金与独立账本（paper_account_info/paper_positions/paper_orders/paper_trades 按 account_id 维度隔离），与 stock/golden_pit 主策略资金互不相融。

#### Scenario: 注册做T账户
- **WHEN** 执行幂等迁移 `_apply_t_account_migration`（数据库初始化/迁移时）
- **THEN** `paper_accounts` 中存在 account_id='t' 的记录，且其独立资金/账本可被 t 账户专用执行器（MarcusVNPyExecutor account_id='t'）访问

#### Scenario: 做T账户与主账户隔离
- **WHEN** 做T账户发生买入/卖出
- **THEN** 只影响 t 账户的资金与持仓，stock/golden_pit 账户的账本、持仓、成交记录不受影响

### Requirement: 独立风控参数分档
系统 SHALL 为 t_account 提供与主账户隔离的独立风控参数集，按保守/标准/激进三档配置，且可随市场环境档位（ACTIVE/CAUTIOUS/HALT）缩放，参数经 ±30% 敏感度扫描标定后固化为上线值，不落静态拍脑袋值。

#### Scenario: 独立参数生效
- **WHEN** t 账户执行做T下单
- **THEN** 风控校验使用 t 账户参数集（单笔占比/单日亏损熔断/买腿vs可卖底仓/单日回转额/单标敞口/冷却期/底仓保留下限等），不套用 stock 账户参数

#### Scenario: 参数分档切换
- **WHEN** market_regime 档位切换（如震荡→单边下跌）
- **THEN** t 账户风控参数按档位缩放（下跌档强制保守档 + 只高抛不低吸 + 冷却延长）

### Requirement: 三层池流转
系统 SHALL 维护做T标的三层池：底仓候选池（未持仓、可T质量打分达标，仅允许建仓）、做T实盘池（已持仓 + 可T质量达标 + 过 regime 门 + 底仓≥保留下限，唯一允许做T触发）、观察池（已持仓但暂不适合做T，仅监控），并支持池间流转。

#### Scenario: 标的进入做T实盘池
- **WHEN** 某标的已持仓、可T质量三代理达标（可T价差空间>0 且有余量、O-C 回归度在可T区间、往返度达标）、通过 regime 门且底仓≥保留下限
- **THEN** 该标的进入做T实盘池，允许生成做T监控条件

#### Scenario: 禁止无底仓标的做T
- **WHEN** 某标的未持仓或底仓低于保留下限
- **THEN** 该标的不得进入做T实盘池，任何做T触发（含低吸/高抛/接回）对其禁用，条件生成器不得为其生成触发条件

#### Scenario: 池间降级流转
- **WHEN** 实盘池标的触发 regime=HALT、底仓跌破保留下限、或可T质量退化
- **THEN** 该标的从做T实盘池转观察池（仅监控不触发）或候选池，做T触发暂停

### Requirement: 可T质量打分制选股
系统 SHALL 用可T质量打分制（而非仅振幅/成交额硬阈值）选择做T标的，打分纳入：可T价差空间（日内振幅中位数 − 2×滑点+手续费，必须>0）、O-C 回归度（|收-开|/振幅）、日内往返度（分钟线折返次数）、流动性（log成交额/换手率适中/5分钟成交均匀度）、风险惩罚（隔夜跳空/涨跌停概率/连板情绪）、成本占比；并用分钟线数据（腾讯 ifzq m5/m1 主、新浪备、brze 权威校验）实算。

#### Scenario: 打分选出做T候选
- **WHEN** 对标的池计算可T质量打分
- **THEN** 可T价差空间>0 作为硬门槛剔除价差不覆盖成本的标的；价差>2.0 优先、O-C 回归度≤0.45 加分、≥0.55 减分，输出可排序分数

#### Scenario: 打分数据源
- **WHEN** 计算可T质量三代理
- **THEN** 使用分钟线数据（腾讯 ifzq m1/m5、新浪、brze stk_mins 任一可用源），日内往返度在 1min 粒度计算（5min 粒度分隔弱）
