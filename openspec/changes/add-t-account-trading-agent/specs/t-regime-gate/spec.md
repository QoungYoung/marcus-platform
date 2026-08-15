## Purpose

做T市场环境闸门：market_regime 三层合成单一总开关（L1 日频基准 + L2 日内动态前哨 + L3 硬保险丝），三态语义与初跌领先预警，量能反向解读，做进监控层写触发事件之前。

## ADDED Requirements

### Requirement: 单一环境闸门三层合成
系统 SHALL 提供 market_regime 单一环境闸门，由三层合成输出一致档位：L1 日频基准（复用现成 market_diagnosis 的 state/score_trend/score_oscillation/score_extreme + Tushare 或腾讯指数日线 MA20/60）、L2 日内动态前哨（腾讯 qt 指数实时跌幅/放量破5日均线/情绪冰点边际变化，分钟级）、L3 硬保险丝（沪深300当日跌>2% 等硬阈值，无条件最高优先）；合成规则：硬保险丝触发→HALT，regime_day=HALT→HALT，regime_intraday=WARN→CAUTIOUS(或HALT)，否则→regime_day。不做多开关并行。

#### Scenario: 硬保险丝无条件关停
- **WHEN** 沪深300 当日跌幅超过硬阈值（如 2%）
- **THEN** 环境闸门无条件转 HALT，低吸类触发全部短路，即使日频基准仍为 ACTIVE

#### Scenario: 日内动态前哨降频
- **WHEN** 指数实时破 5 日均线或情绪边际恶化（日频基准尚未转红）
- **THEN** 环境闸门转 CAUTIOUS（降频 + 只高抛不低吸），不等慢趋势指标确认

#### Scenario: 三态输出一致
- **WHEN** 监控层、Agent、网关查询当前环境档位
- **THEN** 三者读取同一 t_regime_state 合成结果（ACTIVE/CAUTIOUS/HALT），不存在互相矛盾的独立开关

### Requirement: 三态档位语义与策略约束
系统 SHALL 定义三态档位语义并约束做T策略：ACTIVE（震荡市，正常做T，低吸须过复合企稳确认 + 高抛双向）、CAUTIOUS（谨慎/初跌预警，降频 + 只高抛不低吸，低吸触发被短路或挂人工确认）、HALT（单边下跌/系统性风险，无条件禁低吸，只允许高抛减仓或完全停止，且高抛绑定底仓保留下限）。

#### Scenario: 震荡市正常做T
- **WHEN** 档位为 ACTIVE
- **THEN** 做T实盘池标的可正常触发低吸（须过复合企稳确认）与高抛

#### Scenario: 谨慎档只高抛
- **WHEN** 档位为 CAUTIOUS
- **THEN** 低吸触发被短路或仅允许人工确认，仅高抛类触发允许自动

#### Scenario: 关停档禁低吸
- **WHEN** 档位为 HALT
- **THEN** 低吸触发无条件禁止，仅高抛减仓（且不卖穿底仓保留下限）或完全停止

### Requirement: 初跌领先预警
系统 SHALL 提供初跌领先预警：任何"震荡→下跌"快信号（指数放量破5日均线、涨跌停/连板梯队情绪边际恶化、当日指数实时跌幅与量能）命中即先降档至 CAUTIOUS，不等待滞后 1-3 日的慢趋势指标（20日均线/MACD/ADX）确认。

#### Scenario: 快信号先行降档
- **WHEN** 指数放量跌破 5 日均线而 20 日均线尚未走坏
- **THEN** 环境闸门先行转 CAUTIOUS，低吸触发立即降频/短路

### Requirement: 量能反向解读
系统 SHALL 按环境档位对同一量能类指标反向解读（高换手+震荡=做T机会、高换手+单边下跌=出货信号），通过 t_regime_state 的量能解读符号（gate_interpret_sign：+1 机会/-1 出货/0 中性）供监控层乘算后再判断"放量"。

#### Scenario: 同指标不同档位反向解读
- **WHEN** 某标的盘中高换手且当前档位为 HALT（单边下跌）
- **THEN** 该高换手被解读为出货信号（符号 -1），不作为低吸放量依据

### Requirement: 环境闸门前置到监控层
系统 SHALL 将环境闸门做进监控层：TMonitor 每轮在写任何 t_triggers 之前读取 t_regime_state，低吸类条件在 gate_low_buy=BLOCKED 时直接不产生事件、MANUAL_ONLY 时仅允许走 human_confirm 分支；环境判断不交给 Agent 事后判断。

#### Scenario: 监控层前置闸门
- **WHEN** 档位为 HALT 且某条件为低吸类
- **THEN** TMonitor 不写入 t_triggers（gate_low_buy=BLOCKED 短路），即使价格/量能条件已满足

#### Scenario: 谨慎档挂人工
- **WHEN** gate_low_buy=MANUAL_ONLY
- **THEN** 命中的低吸事件仅进入 human_confirm 分支，不自动执行
