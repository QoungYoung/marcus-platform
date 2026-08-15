## 1. 双条件生成（t-condition-generation）

- [ ] 1.1 在 t_pool.py 抽公共函数 `build_t_conditions(cost, amp_med)`：返回 low_buy + high_sell_then_buy_back 两条条件，阈值按 max(下限, amp_med×0.6) 计算（高抛 1.5%、低吸 2.0% 下限），stop_loss_price=成本×0.97
- [ ] 1.2 改造 `t_pool.generate_conditions_for_live_pool`：每标的产出双条件（原单 low_buy 替换为调用公共函数）
- [ ] 1.3 改造 `t_build.auto_gen_conditions_for_build`：建仓盘后生成双条件（调用公共函数）
- [ ] 1.4 改造 `t_backtest._default_t_conditions`：返回双条件（回测以 init_price 为成本；无 m5 数据时用下限阈值）
- [ ] 1.5 单元测试：双条件生成（字段/阈值/止损绑定成本）、低波动标的用下限、高波动标的自适应抬升
- [ ] 1.6 回测验证：t1 四标的窗口 rule 模式核对 trades 出现 side=sell、realized_pnl>0 的闭环笔数

## 2. 止损撮合（t-loss-protection）

- [ ] 2.1 回测 `TBacktestEngine`：每日循环 bar 遍历中新增止损分支——持仓存在且 bar 最低价 ≤ stop_loss_price 时成交止损卖腿（reason=stop_loss，计入 realized_pnl，当日条件冻结 armed=0）
- [ ] 2.2 回测 `_default_t_conditions` 止损价改为绑定 init_price（init_price×0.97），不再 target×0.97 漂移
- [ ] 2.3 生产 `t_monitor.evaluate_condition_at`：主循环前新增止损检查（现价 ≤ stop_loss_price 触发止损卖单，reason=stop_loss）
- [ ] 2.4 高抛卖量约束：高抛成交卖可卖底仓 30%（min 100 股）不卖光；`_process_buyback` 当日未回补次日重新 armed
- [ ] 2.5 单元测试：回测止损触发（事件流含 side=sell reason=stop_loss）、生产止损路径、跳空击穿记录止损缺口事件

## 3. 底仓风控与加仓上限（t-loss-protection）

- [ ] 3.1 `t_gateway` 新增 `_base_loss_guard`：浮亏 ≤ −3% 卖半仓（reason=base_loss_half）、≤ −5% 清仓+当日锁定（reason=base_loss_lock）
- [ ] 3.2 低吸次数上限：`t_gateway.validate_order` 按 date+symbol 计数买腿成交 ≥2 拒绝（reason=加仓次数超限）
- [ ] 3.3 `t_build.build_sizing` 单标上限 10/15/20% → 8/12/18%
- [ ] 3.4 生产开关 `T_STOP_GUARD_ENABLED`（默认开）+ `allow_human_override` 人工覆盖标志
- [ ] 3.5 单元测试：−3% 减半、−5% 清仓锁定、第三次低吸拒绝、单标超限拒绝

## 4. 选股硬过滤（t-selection-filter）

- [ ] 4.1 `t_pool.calc_t_quality` 增加硬性振幅门槛 MIN_AMP_PCT=3.0 / MAX_AMP_PCT=10.0（近 20 日振幅中位，超限 pass_gate=False），MIN_T_SPREAD 0.0 → 0.5
- [ ] 4.2 `t_build._quality_from_daily` 振幅口径对齐：3≤amp≤7 → +0.2、7~10 → +0.1、<3 或 >10 → pass_gate=False 硬拒
- [ ] 4.3 `BUILD_PARAMS_DEFAULT.cand_score_min` 0.55 → 0.65（user 来源 0.60）
- [ ] 4.4 `build_score` 中 trend_gate 触发升级为 pass_gate=False（趋势下行硬排除）
- [ ] 4.5 单元测试：2% 振幅标的拒绝（000001/600900 类）、10%+ 拒绝、价差 ≤0.5 拒绝、0.6 分扫描标的拒绝

## 5. AI 决策策略（t-ai-decision-policy）

- [ ] 5.1 `t_bridge.wake_agent` prompt：弱化无条件保守话术，新增盈亏比 checklist（目标距 ≥0.5%、reward/risk ≥1.2、高抛=兑现正向动作）
- [ ] 5.2 `t_ai_agent._parse_ai_decision` 解析失败/异常：改为规则评审生成默认动作（高抛在 HALT/CAUTIOUS 默认 exec、低吸默认 wait），reason 前缀 `[rule_fallback]`
- [ ] 5.3 `_consecutive_hits` 冷却：exec 胜率 >55% 标的跳过冷却；<40% 提示减仓
- [ ] 5.4 单元测试：乱码/空/非法 reply 返回兜底动作且含 [rule_fallback]；高胜率标的连续命中不冷却

## 6. 回测验证与部署

- [ ] 6.1 rule 模式参数矩阵快跑：选股组合（cand_score_min × MIN_AMP_PCT）× 条件组合（amp_scale × min_high_sell × stop_pct），记录 total_return / realized_pnl / sell 闭环笔数 / max_drawdown
- [ ] 6.2 LLM 精选窗口：best 组合跑 2-4 个历史窗口，对比改造前后 ai_exec_win_rate / abandon / realized_pnl / [rule_fallback] 占比
- [ ] 6.3 全量回归：backend/tests 通过（含 t 相关既有用例）
- [ ] 6.4 部署：docker compose up -d --build backend worker（+frontend 如涉及）；DSH prompt 变更 FORCE_RESEED_PROMPTS=true
