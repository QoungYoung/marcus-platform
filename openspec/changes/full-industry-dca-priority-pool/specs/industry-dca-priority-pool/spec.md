# industry-dca-priority-pool Specification

## Purpose

全行业监测 DCA：把黄金坑定投从"单个指数触发"扩展为"指数 + 全行业双轨"；当多个行业同时入坑且现金不足时，按优先级资金池裁决分配定投金额；坑间资金通过防御承接持续轮动，同时保留现金下限防止摊大饼。

## Requirements

### Requirement: 全行业监测池配置
系统 SHALL 维护一个可配置的全行业监测池（`industry_pool`），每个行业包含：行业 id、名称、贪婪数据代码（`greed_code`）、收益代理（场内 ETF `etf_code` 或场外 `nav_code`）、优先级（`priority`）、定投上限（`max_total`）、最小入坑天数（`min_days_in_pit`）、启用开关。

#### Scenario: 配置一个行业
- **WHEN** 管理员在 `golden_pit_sector_config.industry_pool` 中新增 `industry_innovative_drug`（创新药，greed_code=015916，nav_code=015916，priority=3，max_total=20000）
- **THEN** 系统 SHALL 将该行业纳入全行业监测，并可按 priority/max_total 参与资金池裁决

#### Scenario: 关闭全行业监测
- **WHEN** `golden_pit_sector_config.industry_pool_enabled` 为 false
- **THEN** 系统 SHALL 停止所有行业级 DCA 触发与买入，现有指数级黄金坑 DCA 不受影响

### Requirement: 行业 DCA 触发信号
系统 SHALL 对每个启用的行业独立计算触发信号：250 日贪婪分位（`greed_pct`）与价格超跌（`drawdown` = 收盘价距 N 日高点的回撤），两者同时满足才触发 DCA 窗口（贪婪历史不足 20 天时仅按价格超跌触发）。

#### Scenario: 双条件触发
- **WHEN** 某行业 greed_pct <= 0.15 且 drawdown >= 20%
- **THEN** 系统 SHALL 为该行业开启独立 DCA 窗口（窗口期 15 天，按 dca_strategy 权重摊投）

#### Scenario: 贪婪历史不足时仅价格触发
- **WHEN** 贪婪历史少于 20 个观测且 drawdown >= 20%
- **THEN** 系统 SHALL 仍按价格超跌触发 DCA 窗口

#### Scenario: 过热过滤
- **WHEN** 某行业 250 日贪婪分位 > `industry_entry_cap`（默认 0.85）
- **THEN** 系统 SHALL 跳过该行业当日买入（不开启/不继续新窗口）

### Requirement: 资金池优先级裁决
系统 SHALL 每日先汇总所有 in-pit 行业（含指数级标的）当日计划定投金额；若计划总和超过可用现金（账户净值 × (1 - `cash_min_pct`)），按（tier 优先级，priority）从高到低逐个分配计划金额，直到现金耗尽，未分到的当日跳过、额度保留次日。

#### Scenario: 并发坑位超现金
- **WHEN** 当日 3 个行业计划金额合计 6 万，可用现金 3 万
- **THEN** 系统 SHALL 按优先级从高到低逐个分配共 3 万（高优先级行业优先全额分配），低优先级行业当日跳过、额度滚动次日

#### Scenario: 现金充足
- **WHEN** 当日计划金额合计小于可用现金
- **THEN** 系统 SHALL 按各自计划全额执行，不裁剪

#### Scenario: 现金下限保护
- **WHEN** 账户净值 25 万且 `cash_min_pct`=0.2
- **THEN** 系统 SHALL 保留至少 5 万现金不参与定投分配

### Requirement: 坑间资金流转
行业 DCA 出场（止盈/兜底）后，释放资金 SHALL 按 `DEFENSE_TAKEOVER_WEIGHTS` 等权轮入防御组合；新行业触发 DCA 且现金不足时，系统 SHALL 从防御持仓按比例赎回回补。

#### Scenario: 出场轮入防御
- **WHEN** 某行业 DCA 窗口出场释放 1 万
- **THEN** 系统 SHALL 将该 1 万按防御组合权重（红利/银行/黄金/国债/有色各 20%）等权轮入

#### Scenario: 新坑回补
- **WHEN** 新行业触发 DCA 但可用现金不足
- **THEN** 系统 SHALL 按防御持仓比例赎回回补目标差额，再执行定投

### Requirement: 全行业现状展示
系统 SHALL 提供只读接口输出全行业监测现状：每个行业的贪婪分位、超跌幅度、是否 in-pit、DCA 窗口进度、当日计划/实际金额、累计投入。

#### Scenario: 查询行业现状
- **WHEN** GET 黄金坑 status 接口（industry_pool_enabled=true）
- **THEN** 响应 SHALL 包含 industries 数组，每项含 greed_pct、drawdown、in_pit、window_day、planned_amount、actual_amount、total_invested
