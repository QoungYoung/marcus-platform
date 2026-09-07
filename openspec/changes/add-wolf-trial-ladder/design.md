## Context

现状：wave=t_only 下系统拦截"新建仓"，只有做T低吸(T仓)；但狼大磨底期实际用"低位埋伏(先手小仓)+低吸试仓+做T在场+等右侧"，且会卖旧换新。已具备零件：
- position_class(概念 LOW) + stock_confirm_result(TOP3 分层, stage∈缩量止跌/结构到位/突破候选/确认)
- fusion 每日 score/TOP (main_line_state.json) + THEME_CONCEPTS + stock_concept_map(概念表) 
- TMonitor 253/254 无底仓→wolf_253_build 建小底仓路径
- wave_state.operation(gate)、no_t_gate(G3 板块收敛拦卖腿, sector_g3_state.json)、T出撤销式(已上线)
- 上一轮已设计 docs/trial-tranche-plan.md(试仓档) 与 docs/wolf-no-t-gate-plan.md
缺口=把它们串成"三档建仓状态机+动态升级+卖旧切换"的可执行链，且全部方向/阈值运行时化、杜绝 4000/4020 类点位硬编码。

## Goals / Non-Goals

**Goals:**
- 三档状态机 ambush→trial→normal（档位判定、额度记账、低吸触发限定、defense/exit 不介入）。
- 动态升级信号（wave=build 或结构主升确认：close>MA200 ∧ MA20>MA60 ∧ vol≥1.2×20均 ∧ close≥60日箱体上沿×0.995，上沿滚动计算）。
- switch 评估（旧方向掉出TOP3+走弱→sell_old；新主线突破候选→buy_new；强的留弱的丢）——先 DRY 清单，默认不自动执行。
- 报告：三档状态/额度/升级信号/switch 清单。
- 方向自动解析（fusion TOP1∪TOP2 + 概念表/名称关键词），零标的硬编码；全部阈值 env/config。

**Non-Goals:**
- 不改 wave gate 对"追高 buy"的拦截语义（本链只在低吸触发放行 ambush/trial）。
- 不做个股级高低位分类器（position_class 概念级已够方向过滤）。
- 不自动接通 switch 执行（本 change 只 DRY 清单+布腿建议；执行接通另改）。
- 不把"4000-4020"等点位写入任何代码/配置（语料描述不落规则）。

## Decisions

1. **档位状态存储**：data/tranche_state.json（{symbol/方向: {tier, bought_amt, batches, updated}}），盘前由调度重算确认 stage 变化升级/降级；额度用 paper_trades 未卖出净额交叉校验。
2. **触发限定**：只认 trigger_kind ∈ {custom_prevlow(254), custom_m5dump(253)} 且 wave∈{t_only,side}；防御/退出档直接不参与。买量=ambush/trial 上限与原有 30% 做T量取小？→ 采用"档位上限优先"：ambush≤1%/10%、trial≤2%/30%（env），单次还受 _max_buy_volume 约束。
3. **升级信号**：函数 escalate_signal() 读上证日线(已有 index CSV/数据源)算 MA200/MA20/MA60/20日均量/60日箱体上沿——全部滚动计算，无字面量点位；窗口(200/20/60)与倍数(1.2/0.995)进 env。
4. **方向解析**：方向集=fusion TOP1∪TOP2(当日)；板块归属复用 apps/main_line/sector_g3.symbol_themes（概念表+ETF名称关键词），不新增硬表。
5. **switch DRY**：默认只写 data/switch_builder_plan.json + 报告提示；执行(卖腿/建仓)需 env SWITCH_AUTO_EXEC=1 才接通（默认 0），先对照狼大数日。
6. **与既有协同**：G3 收敛门(no_t_gate)照常拦卖腿；T出撤销式照常；本链只负责"低吸建仓额度/档位/切换编排"，不触碰做T执行细节。

## Open Questions
- ambush 档是否允许在 4-4 磨底期长期持有(数月)还是仅短线先手？→ 默认跟随档位：ambush 破前低/confirm 证伪即止损，不设时间强平(狼大埋伏可持)（可配 AMBUSH_HOLD_DAYS_MAX 默认0=不限）。
- switch 的 sell_old 是否与 rotation sell_legs 合并？→ 本 change 输出清单供 rotation 消费；执行合并后续。
