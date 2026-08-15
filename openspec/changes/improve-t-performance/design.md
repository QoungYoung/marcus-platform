## Context

动机见 proposal.md（回测 #24：-4.67%、0 次高抛、realized_pnl=0、低吸加仓放大亏损 67%、低波标的穿透、止损不生效）。现状约束：

- **条件生成三处**：`t_pool.generate_conditions_for_live_pool`（生产 live 池）、`t_build.auto_gen_conditions_for_build`（建仓盘后）、`t_backtest._default_t_conditions`（回测），全部只产 `trigger_kind="low_buy"` 单条件，`sell_target_price` 仅作字段写入。
- **卖腿撮合机制已存在**：`t_monitor.evaluate_condition_at`（现价 ≥ sell_target_price 命中 high_sell_then_buy_back）、回测 `TBacktestEngine._handle_trigger` 的 `high_sell_then_buy_back` 分支 + `_process_buyback`（回补价 ×0.996）——缺的只是"产条件"。
- **止损不消费**：`stop_loss_price` 生产/回测均无独立撮合分支；回测 `stop = target×0.97` 基于低吸价推导，位置漂移。
- **选股双口径**：生产 `calc_t_quality`（t_pool.py:39，四维），回测 `_quality_from_daily`（t_build.py:252，简化振幅/流动性），振幅 1%~3% 区间无惩罚 → 2% 振幅标的 score≈0.7 穿透；`MIN_T_SPREAD=0.0` 过松。
- **决策链路**：`t_bridge.wake_agent` prompt（强保守框架）、`t_ai_agent._parse_ai_decision`（解析失败一律 wait）、`_consecutive_hits` 冷却（AI_CONSECUTIVE_HIT_ALERT=3）。
- **风控**：`t_gateway` 有日亏 2% 硬闸、连亏≥2 强制放弃；单标上限 10/15/20%；无底仓浮亏止损、无低吸次数上限。

## Goals / Non-Goals

**Goals:**
- 让高抛卖腿触发产生（条件生成双条件），做T闭环兑现利润
- 止损价真实进入生产/回测撮合，绑定实际成交成本
- 底仓浮亏 −3%/−5% 减半/清仓锁定，限制低吸加仓 ≤2 次/日
- 选股硬过滤（振幅 3~10%、价差 >0.5%、趋势硬排除、门槛 0.65）两口径对齐
- AI 决策盈亏比导向 + 解析失败规则兜底 + 高胜率放冷

**Non-Goals:**
- 不改动撮合引擎核心（VNPy paper engine / 回测撮合基础设施）
- 不做历史数据迁移（条件表/审计表结构不变，仅新增条件条目与 reason 标注）
- 不改 A 股 T+0 交易规则本身（T+1 底仓当日不可卖，高抛卖的是可用底仓、低吸买回当日锁定）
- 不实现多账户灰度（生产默认全量，但风控开关可配）

## Decisions

### D1: 双条件生成，共用参数函数（一处公式，三处调用）
在 `t_build.py`（或 `t_pool.py` 的 `_calc_daily_amplitudes` 附近）抽公共函数 `build_t_conditions(cost, amp_med)`：
- 高抛：`trigger_kind="high_sell_then_buy_back"`，`target_price = round(cost × (1 + max(1.5%, amp_med × 0.6)), 2)`
- 低吸：`trigger_kind="low_buy"`，`target_price = round(cost × (1 − max(2.0%, amp_med × 0.6)), 2)`
- 共用 `sell_target_price`（高抛触发价）、`stop_loss_price = round(cost × 0.97, 2)`（绑定成本）、回补价沿用 `_process_buyback` 的 ×0.996
- 三处调用点替换：`generate_conditions_for_live_pool`、`auto_gen_conditions_for_build`、`_default_t_conditions`（回测以 init_price 为 cost）
- amp_med 来源：生产 live 池已有近 6 日 m5 振幅计算；回测用预取 m5 数据窗口计算，无 m5 时退化为下限阈值
- **替代方案**：只在回测加高抛（改 `_default_t_conditions`）。否决——生产不同步则实盘继续只低吸，回测结论无法迁移。

### D2: 止损撮合——回测新增独立分支，生产挂在监控主循环
- 回测 `TBacktestEngine._handle_trigger`（或每日循环 bar 遍历）新增：持仓存在且 bar 最低价 ≤ `stop_loss_price` 时，以 `max(bar 开盘价, stop_loss_price)` 或 bar 收盘价成交止损卖腿（整笔或按 P0-3 减半比例），`reason="stop_loss"`，计入 realized_pnl，当日该标的条件冻结（armed=0）
- 生产 `t_monitor.evaluate_condition_at` 主循环前新增止损检查分支（与低吸/高抛同粒度），`stop_loss_price` 从条件表读取
- 高抛卖量：可卖底仓 30%（min 100 股），不卖光底仓；低吸条件数量约束由 D4 风控承担
- **替代方案**：止损挂在 `t_gateway.validate_order` 被动触发。否决——无订单时深跌不触发，必须主动扫描。

### D3: 选股口径对齐——参数化硬门槛，回测复用生产逻辑
- `t_pool.calc_t_quality` 增加硬性振幅门槛：`MIN_AMP_PCT=3.0`、`MAX_AMP_PCT=10.0`（近 20 日振幅中位），`MIN_T_SPREAD=0.5`，超限 `pass_gate=False`
- `t_build._quality_from_daily` 振幅贡献段改为与 calc_t_quality 同语义：`3≤amp≤7 → +0.2`，`7~10 → +0.1`，`<3 或 >10 → pass_gate=False`（硬拒，不再给 0.7 分）
- `BUILD_PARAMS_DEFAULT.cand_score_min`：0.55 → 0.65；`build_score` 中 `trend_ok=False`（trend_gate 触发）升级为 `pass_gate=False`
- 常量集中放 `t_pool.py` 模块级（回测 import 引用，避免双份漂移）
- **替代方案**：回测直接调 `calc_t_quality`。否决——回测无实时换手率/成交额口径，需降级分支；参数对齐即可消除穿透。

### D4: 风控落地位置
- 底仓止损：`t_gateway` 新增 `_base_loss_guard`（或 `t_monitor` 独立扫描）：浮亏 ≤ −3% 卖半仓（reason=base_loss_half）、≤ −5% 清仓+当日锁定（reason=base_loss_lock）；执行入口复用现有下单封装
- 低吸次数上限：`t_gateway.validate_order`（或 `t_ai_agent` exec 路径）按 `date+symbol` 计数买腿成交，≥2 拒绝（reason=加仓次数超限）
- 单标上限：`t_build.build_sizing` 常量 per_symbol_cap 10/15/20 → 8/12/18
- 生产开关：`T_STOP_GUARD_ENABLED` env 默认开，便于灰度回退

### D5: AI 决策策略
- prompt（`t_bridge.wake_agent` 的 T_BUILD_SYSTEM_PROMPT / BACKTEST_REVIEW_PROMPT）：弱化无条件保守话术，新增盈亏比 checklist（目标距 ≥0.5%、reward/risk ≥1.2、高抛=兑现正向动作）；`_symbol_t_stats` 上下文把 exec 胜率 >55% 标的标注"可重触发"
- `t_ai_agent._parse_ai_decision` 解析失败/异常：不再一律 wait，调用规则评审（复用 `classify_escalation`/`_rule_review` 的 regime/连亏/日亏信号）生成默认动作——高抛触发在 HALT/CAUTIOUS 默认 exec、低吸默认 wait；reason 前缀 `[rule_fallback]`
- `_consecutive_hits` 冷却：exec 胜率 >55% 标的跳过冷却；<40% 提示减仓
- **替代方案**：只改 prompt 不动解析。否决——LLM 输出不可靠，无兜底则高抛触发仍会被 wait 卡死。

### D6: 回测验证方案（rule 矩阵 + LLM 精选）
- rule 模式参数矩阵：选股 `cand_score_min ∈ {0.55,0.60,0.65}` × `MIN_AMP_PCT ∈ {2.5,3.0,3.5}`（9 组合，可抽子集），条件 `amp_scale ∈ {0.5,0.6,0.7}` × `min_high_sell ∈ {1.5,2.0}` × `stop_pct ∈ {3.0,5.0}`；指标：total_return / realized_pnl / sell 闭环笔数 / max_drawdown
- LLM 精选：best 组合跑 2-4 个历史窗口对比改造前后（ai_exec_win_rate / abandon / realized_pnl / [rule_fallback] 占比）
- 沿用现有 runner 的 `rule`/`llm` review 模式，无需新基础设施

## Risks / Trade-offs

- [高抛 +1.5% 下限在低波标的上命中率低] → 与 D3 选股硬门槛联动（振幅 <3% 不进池）；矩阵验证 `min_high_sell` 与 `amp_scale`
- [高抛卖光底仓导致无仓可 T / 回补失败] → 高抛卖量 ≤ 可卖底仓 30%；`_process_buyback` 当日未回补次日重新 armed（需在 day_conds 初始化保证）
- [止损在 m5 粒度被跳空击穿] → 记录止损缺口事件；A 股跌停无法卖出的场景审计 reason 标注
- [选股收紧导致空池/空仓期长] → 门槛分层：user 来源 0.60 / scan/pool 0.65；矩阵对比空仓天数
- [prompt 变长降低解析稳定性] → D5 兜底保证解析失败仍按规则动作，不回退到全 wait
- [底仓止损错杀反弹] → −5% 清仓允许人工覆盖开关（`allow_human_override`）；生产开关灰度 1 周

## Migration Plan

1. 纯代码改动 + 常量/参数调整，无表结构变更、无数据迁移
2. 本地单测覆盖：双条件生成、止损撮合、门槛拒绝、兜底动作、次数上限
3. 部署：`docker compose up -d --build backend worker`（+ frontend 如涉及报告展示）；DSH prompt 变更需 `FORCE_RESEED_PROMPTS=true` 重建
4. 回滚：参数/开关回退（`T_STOP_GUARD_ENABLED=false`、门槛还原），代码回滚走 git revert
