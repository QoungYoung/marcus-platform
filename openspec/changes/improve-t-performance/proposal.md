## Why

回测 #24（每日滚动建仓，3 个月，LLM 复核）收益 **-4.67%**、AI 执行胜率 47.62% 但做T **realized_pnl=0**。AgentTeams 三阶段审查（数据诊断/策略审查/优化方案，见 `docs/t-optimization-plan.md`）定位到代码级根因：**全部 253 次触发都是 low_buy 低吸、0 次高抛触发**——三个条件生成器只产 `trigger_kind=low_buy`，做T变成单向向下摊平，21 笔成交全是买腿从未卖出兑现；叠加低吸加仓把浮亏放大 67%、选股无硬性振幅下限放行低波动标的、止损字段从不被消费，导致"胜率 47% 却整体亏损"。

## What Changes

- **条件生成器产「低吸+高抛」双条件**（P0-1）：`t_pool.generate_conditions_for_live_pool` / `t_build.auto_gen_conditions_for_build` / `t_backtest._default_t_conditions` 从单 `low_buy` 条件改为生成 `low_buy` + `high_sell_then_buy_back` 两条，让卖腿闭环（高抛触发 → 卖出兑现 → 当日回补）真正产生；高抛触发价 = cost×(1+max(1.5%, amp_med×0.6))
- **止损真正进入撮合**（P0-2）：回测引擎 `_handle_trigger` 新增基于 `stop_loss_price` 的止损卖腿分支（mark-to-market），生产侧 `evaluate_condition_at` 同步；止损价绑定实际成交/持仓成本而非 `target×0.97` 漂移位置
- **底仓止损独立于做T**（P0-3）：`t_gateway`/`t_monitor` 新增底仓风控——浮亏 −3% 自动减半仓、−5% 清仓并当日锁定该标的
- **选股硬过滤**（P1）：`_quality_from_daily`（回测）与 `calc_t_quality`（生产）增加硬性振幅下限/上限（MIN_AMP_PCT=3 / MAX_AMP_PCT=10，近 20 日振幅中位 <3% 或 >10% 硬拒），MIN_T_SPREAD 0.0→0.5；`cand_score_min` 0.55→0.65；`trend_gate` 从乘性扣分升级为硬排除
- **条件波动率自适应**（P2）：高抛/低吸/止损阈值按近 6 日 m5 振幅中位动态化（amp_scale=0.6，min_high_sell=1.5%，min_low_buy=2.0%，stop=3.0%）
- **AI 决策层修正**（P3）：`t_bridge` prompt 改盈亏比导向（现价距目标 ≥0.5% 且覆盖成本才 exec）+ 高抛激励；`_parse_ai_decision` 解析失败从一律 wait 改为规则默认动作并标注 `[rule_fallback]`；exec 胜率 >55% 的标的放开连续命中冷却
- **风控加固**：低吸加仓次数上限 ≤2 次/日/标的；单标累计仓位上限 10/15/20% → 8/12/18%
- **回测验证**：rule 模式参数矩阵快跑（选股 9 组合 × 条件 12 组合）+ LLM 精选窗口 A/B 对比

## Capabilities

### New Capabilities
- `t-condition-generation`: 做T条件生成——每标的同时产出低吸+高抛双条件、按标的波动率自适应阈值、止损绑定实际成交成本
- `t-selection-filter`: 选股硬过滤——振幅下限/上限硬门槛、可T价差门槛、趋势下行硬排除、候选分数门槛提高
- `t-loss-protection`: 止损执行与底仓风控——止损价真实触发撮合、底仓浮亏 −3%/−5% 减半/清仓、低吸加仓次数上限
- `t-ai-decision-policy`: AI 做T决策策略——盈亏比导向 prompt、解析失败规则兜底、高胜率标的重触发放开

### Modified Capabilities
<!-- 主 specs 中暂无 t-* 能力（T 相关 delta 尚未归档），本变更全部为新能力，无修改项 -->
- 无

## Impact

- **代码**：`backend/app/services/t_pool.py`（条件生成/calc_t_quality 门槛）、`t_build.py`（auto_gen_conditions_for_build/_quality_from_daily/build_score/cand_score_min/build_sizing）、`t_backtest.py`（_default_t_conditions/_handle_trigger 止损分支/组合回测）、`t_monitor.py`（evaluate_condition_at 止损）、`t_gateway.py`（底仓风控/加仓上限）、`t_ai_agent.py`（解析兜底）、`t_bridge.py`（prompt/冷却）
- **配置**：BUILD_PARAMS_DEFAULT（cand_score_min）、t_pool 常量（MIN_AMP_PCT/MAX_AMP_PCT/MIN_T_SPREAD）、风控参数（stop_loss_pct、加仓上限、单标上限）
- **数据**：回测事件流新增 sell/stop_loss 成交记录；t_ai_actions 新增 `[rule_fallback]` 标注
- **前端**：回测报告 metrics 增加 sell 闭环笔数、realized_pnl、止损次数展示（如需要）
- **不破坏**：现有条件/决策数据结构兼容（新增条件条目而非改字段语义），回测任务表无结构变更
