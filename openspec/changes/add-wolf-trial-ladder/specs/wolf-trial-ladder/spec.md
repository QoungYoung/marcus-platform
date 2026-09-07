## Purpose

在 wave=t_only（大4浪磨底）期间，按狼大实际打法提供"低位埋伏→试仓→主升建仓"的三档建仓执行链与"卖旧切新"的换仓编排：只允许低吸触发建仓、禁止追高；升级判定用动态结构信号而非固定点位；全部阈值与方向集合运行时配置，杜绝硬编码。

## ADDED Requirements

### Requirement: 三档仓位状态机
系统 SHALL 为每个进入本链的标的/方向维护三档状态：ambush（低位埋伏先手）、trial（试仓）、normal（主升建仓），并依据该标的确认阶段自动在档位间移动。

#### Scenario: 低位标的进入埋伏档
- **WHEN** 标的属 fusion TOP1∪TOP2 主线候选 且 position_class 判 LOW 且 confirm stage∈{缩量止跌,结构到位} 且 当日缩量(量比≤AMBUSH_SHRINK_MAX, 默认0.7)且不破前低 且 非拥挤黑名单
- **THEN** 系统将标的置于 ambush 档，允许低吸买入（单次≤净值 AMBUSH_SINGLE_PCT 默认1%，累计≤计划仓 AMBUSH_CAP_PCT 默认10%，分批）
- **AND** 系统对该标的记录档位状态与已用额度

#### Scenario: 突破候选进入试仓档
- **WHEN** 标的 confirm stage∈{突破候选,确认}（或 ambush 档标的触发突破/确认）
- **THEN** 系统将其升级至 trial 档，低吸买入累计上限放宽至计划仓 TRIAL_CAP_PCT（默认30%，单次≤TRIAL_SINGLE_PCT 默认2%）
- **AND** ambush 档已用额度计入 trial 累计，不重复放开

#### Scenario: 主升确认进入正常档
- **WHEN** 升级信号成立（见 Requirement: 动态升级信号）
- **THEN** 系统将档位升至 normal，按主线正常建仓额度执行（不再受 ambush/trial 上限约束）

#### Scenario: 非低吸触发不得建仓
- **WHEN** 触发来源不是 254(custom_prevlow 触前低+缩量) 或 253(custom_m5dump 大盘5min急杀≥阈值) 的低吸类买腿
- **THEN** 系统不得按 ambush/trial 放行建仓（追高式 buy 仍被 wave/t_only gate 拦截）
- **AND** wave∈{defense,exit} 时不进入/维持任何档位（防守/兑现期纪律优先）

### Requirement: 低吸触发与额度记账
系统 SHALL 仅经低吸触发（254/253）执行本链买入，并记账每标的/方向已用试仓额度（基于 paper_trades 未卖出净额或持久化档位状态）。

#### Scenario: 分批低吸
- **WHEN** 标的处于 ambush/trial 且再次出现 254/253 低吸信号且未超累计上限
- **THEN** 系统放行该批低吸买入，并更新额度累计
- **AND** 达到累计上限后不再放行，直至档位升级或卖出释放额度

### Requirement: 动态升级信号（杜绝点位硬编码）
系统 SHALL 依据动态结构信号判定 ambush/trial → normal 升级；信号阈值仅来自滚动行情与配置，禁止任何固定指数点位字面量（如 4000/4020）。

#### Scenario: wave 升级
- **WHEN** data/wave_state.json 的 operation 变更为 build
- **THEN** 升级信号成立（动态，由 wave_agent 结构判定给出）

#### Scenario: 结构主升确认
- **WHEN** 上证(000001.SH) 满足全部：close > MA200 且 MA20 > MA60 且 当日 vol ≥ ESCALATE_VOL_RATIO(默认1.2)×20日均量 且 close ≥ 近60日最高(max(high[-60:]))×0.995（上沿随行情滚动）
- **THEN** 升级信号成立
- **AND** 系统不得将上述结构值固化为常量点位；窗口与倍数经 config/env 调整时随新窗口重新计算

#### Scenario: 无升级不自动加仓
- **WHEN** 升级信号不成立且标的未出现新低吸触发
- **THEN** 系统保持当前档位与额度，不自动加仓、不追高

### Requirement: 方向与板块归属自动解析（杜绝标的硬编码）
系统 SHALL 从运行时数据自动解析"持仓/标的 → 主线候选方向(TOP1∪TOP2)"与"标的 → 板块主题"，不得维护持仓→ETF/持仓→方向 的硬编码表。

#### Scenario: 方向过滤
- **WHEN** 判断某标的可否进入本链
- **THEN** 系统用 fusion TOP1∪TOP2（读 main_line_state.fusion 排序前2）过滤：属 TOP1∪TOP2 才可进入
- **AND** TOP1∪TOP2 由当日 fusion 动态决定

#### Scenario: 板块归属
- **WHEN** 需要标的所属板块主题（与 G3 收敛门、报告联动）
- **THEN** 系统经 stock_concept_map 概念名 ∩ THEME_CONCEPTS 归属；ETF/无概念行用其名称关键词（主题级关键词映射）归属——不得以具体标的代码写分支

### Requirement: 切换卖旧建新评估（switch，先 DRY）
系统 SHALL 评估"旧方向走弱卖出 + 新方向建仓"的换仓清单并输出（先 DRY 记录，不直接执行，对照狼大后再接通）。

#### Scenario: 旧方向走弱标记
- **WHEN** 持仓标的所属方向掉出 fusion TOP3 且（个股破位：收盘跌破 MA20 或近5日资金流负 或 confirm 证伪）
- **THEN** 系统将该持仓标为 sell_old（建议布卖腿：黄线/破位确认），释放资金
- **AND** 相对新主线仍强（未破位且资金非负）的持仓标为 keep（"强的留"）

#### Scenario: 新方向建仓候选
- **WHEN** 新上榜/仍在 TOP1∪TOP2 的主线方向出现 stock_confirm 突破候选/确认活跃股 且 非拥挤
- **THEN** 系统将其列入 buy_new（建议经 254/253 低吸建新仓，无底仓走 wolf_253_build 小底仓路径），额度受 ambush/trial 档约束
- **AND** 输出 buy_new 前建议使用卖旧释放资金，额度记账

#### Scenario: 清单输出（DRY）
- **WHEN** switch 评估完成
- **THEN** 系统输出 {sell_old, keep, buy_new, 各档位/额度, 升级信号} 至报告/清单文件(data/switch_builder_plan.json)供人工与复盘对照
- **AND** 默认不自动执行买卖（DRY），经配置/人工确认后才接通执行

### Requirement: 报告与审计
系统 SHALL 在盘前/复盘报告输出本链状态：可埋伏/试仓方向与标的、各档位已用/剩余额度、升级信号是否成立、switch 清单。

#### Scenario: 报告输出
- **WHEN** 生成盘前或复盘报告
- **THEN** 报告包含三档状态（ambush/trial/normal 标的名与批次）、额度使用率、升级信号(yes/no 与触发项)、switch 建议清单(sell_old/keep/buy_new)
