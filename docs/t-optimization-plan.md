# 做T系统可落地优化方案（t3）

> 依据 t1 数据诊断 + t2 策略审查，基于实际代码核验后设计。
> 原则：改动落在现有代码（t_build / t_pool / t_backtest / t_gateway / t_ai_agent / t_bridge），
> 优先低风险高杠杆，按优先级排序；每条给出改动点、参数值、预期影响、风险、验证方式。

## 0. 代码核验结论（t3 重要发现，补充 t1/t2）

对全链路源码逐一核验后确认核心结构缺陷，并定位到具体行：

- **做T条件全部是 `trigger_kind="low_buy"`，从不存在卖腿条件的生产者。**
  - `t_pool.generate_conditions_for_live_pool`（t_pool.py:300-336）：`trigger_kind="low_buy"` 单类型，`sell_target_price=1.015` 仅作为字段写入，无任何 `high_sell_then_buy_back` 条件产生。
  - `t_build.auto_gen_conditions_for_build`（t_build.py:911-944）：同样只生成 `trigger_kind="low_buy"`。
  - `t_backtest._default_t_conditions`（t_backtest.py:930-939）：返回单元素 `trigger_kind="low_buy"`。
- **但卖腿撮合机制是存在的、只是没被触发**：`t_monitor.evaluate_condition_at`（t_monitor.py:643-644）已支持 `high_sell_then_buy_back`（现价 ≥ `sell_target_price` 才命中）；回测引擎 `TBacktestEngine._handle_trigger`（t_backtest.py:736-741）已支持 `high_sell_then_buy_back` 触发后挂买回单（`_process_buyback` 795）。即**产腿 + 撮合闭环代码具备，唯一缺口是"条件生成器只产低吸、不产高抛"**。
- **止损字段 `stop_loss_price` 生产/回测均不被消费**：回测 `_handle_trigger` 只按 `trigger_kind` 撮合，无止损触发分支；生产侧 `evaluate_condition_at` 亦无基于 stop_loss_price 的独立停损路径。底仓深跌全程扛单。
- `build_score` 生产用 `calc_t_quality`，回测用 `_quality_from_daily`（已确认 t_backtest.py:1037/1192）。`_quality_from_daily` 振幅贡献在 1%~3% 与 7%~12% 区间为 0（不惩罚），流动性 ≥8 亿即 +0.2 → 日均振幅 ~2% 的低波标的可得 0.7 分 → `build_score=0.7*0.8+0.1*0.05=0.575`（trend 过时 trend_add=0.1）≥ 0.55 门槛穿透。与 t2 P0-1 一致。

**→ 方案第一条（最高杠杆）必须是"建立高抛触发"，否则做T永不兑现利润。** 其余选股/止损/风控为二阶。

---

## P0 优先级（最高杠杆，先做）

### P0-1【核心】条件生成器支持"高抛卖腿"，使高抛能够触发

**改动点：**
1. `t_pool.generate_conditions_for_live_pool`：为每个 live 池标的**生成 2 条条件**——`low_buy`（低吸）+ `high_sell_then_buy_back`（高抛回补）。两条条件共享 `target_price`/`sell_target_price`。
2. `t_build.auto_gen_conditions_for_build`：同样生成两条（建仓当日盘后 → D+1 条件）。
3. `t_backtest._default_t_conditions`：返回两条（low_buy + high_sell_then_buy_back），供回测 rule/LLM 验证。
4. 生成高抛条件时把 `sell_target_price` 作为高抛触发价（`target_price` 语义在 `high_sell_then_buy_back` 是卖价触发）。

**参数值建议（首版保守）：**
- 高抛触发价 `sell_target_price = cost × (1 + max(1.5%, amp_median × 0.6))`，其中 `amp_median` = 近 6 日 m5 聚合日内振幅中位数（复用 `t_pool._calc_daily_amplitudes`）。对 2% 振幅标的 = cost×(1+1.5%)，1.5% 阈值仍是下限（薄盈标的至少覆盖成本）；对 5%+ 振幅标的自动抬高到叠加真实波动。
- 低吸触发价 `target_price = cost × (1 − max(2%, amp_median × 0.6))`。
- 高抛回补价（buy-back）`= sell_exec × (1 − 0.4%)`（沿用现有 `_process_buyback` 0.996 逻辑）。

**预期影响：** 直接解决 t1 "0 次高抛、realized_pnl=0" 结构性缺陷——产生卖腿触发的可能，让做T从"单向向下摊平"变为"低吸加筹码、高抛兑现 T+0 闭环利润"。这是收益转正的关键一步。

**风险：** 规则值 +1.5% 高抛在 2% 振幅标的可能难以触及（命中率低），需与选股硬门槛（P1-1）联动——只让振幅足够大的标的进池。需防高抛后不回补导致底仓被卖光（回落到底仓保护）。

**验证：** 回测 rule 模式跑 t1 四标的窗口，核对 `trades` 出现 `side=="sell"`、`realized_pnl>0` 的闭环笔数上升、`win_rate_pct` 从 0 变为非零；`ai_exec_count` 中 sell 占比上升。

### P0-2【核心】回测"高抛"撮合 + 止损真正进入撮合

**改动点：**
1. `TBacktestEngine._handle_trigger`：已支持 `high_sell_then_buy_back`（736 行分支），但需确保数量逻辑正确——高抛卖量建议 = 可卖底仓的 30%（不能把整仓卖光导致后续无可卖），且高抛成交后 `_pending_buyback` 挂单若当日未回补，次日应允许按新条件重新触发（当前 782 行"挂单作废"合理，但次日无重挂机制 → 在 day_conds 初始化时若底仓 >0 保证高抛条件仍 armed）。
2. **新增止损撮合分支**：在每日循环每根 bar 前，若 `ledger` 存在持仓且当前价 ≤ `stop_loss_price`（绑定实际成交/持仓成本，见 P1-4），触发 `do_sell` 止损卖腿（整笔底仓或减半），计入 realized_pnl；止损后当日该标的高抛/低吸条件冻结（arm=0）。
3. 回测引擎 `_review`「规则模式」对齐新语义：`high_sell` 触发不应被默认 abandon——`_rule_review` 需区分买卖腿：HALT 时高抛应 `exec`（兑现离场），CAUTIOUS 时高抛 `exec`；只有低吸在 CAUTIOUS(H)、HALT 才 abandon。

**参数：** `stop_loss_pct = 3.0`（对持仓成本，见 P1-4 绑定实际成交价）。

**预期影响：** 让回测能实际上"剪切"深跌亏损，P0-3 的底仓止损效果可在回测中先验证（000001 类型标的 -29% 扛单可被 -3% 或 -5% 止损/减半截断）。

**风险：** 止损滑块在日内 m5 粒度可能被跳空击穿（一次跳过止损价），需记录"止损缺口"事件并评估；A股跌停可能无法卖出止损。

**验证：** 回测核对事件流出现 `trade` 侧=sell 且 reason=stop_loss；对比加/不加止损的总收益与 max_drawdown。

### P0-3【风控】底仓止损（独立于做T）

**改动点：**`t_gateway` 新增底仓风控（或 `t_monitor` 独立停损扫描线程）：
- 对 t 账户持仓逐标的监控"浮亏（现价 vs avg_price）"。
- 触发 `−3%`：自动减半仓（卖 50%）；`−5%`：清空底仓并当日锁定该标的（禁再建/禁低吸）。
- 在 `validate_order`/`validate_build_position` 前增加 `_base_loss_guard` 检查：任何买腿/建仓前先评估当前标的浮亏，若 ≤ −5% 直接 `blocked`；≤ −3% 先执行减半再放行买腿。

**参数：** 减半触发 `-3.0%`，清仓/锁定 `-5.0%`，锁定持续至次日或浮亏回正。

**预期影响：** 直接遏制 t1 "601318 单只 −4105（占亏损 44%）、000001 深跌 −29% 全程扛单"——底仓深跌被截断，回撤显著收窄。

**风险：** 止损在反弹后可能错杀（把本该回本的仓清了）。因其独立于做T，可用 -5% 清仓权限也允许人工覆盖（flag `allow_human_override`）。

**验证：** 回测历史窗口对照底仓止损 on/off 的 max_drawdown 与单标的 realized_pnl；生产灰度观察 1 周。

---

## P1 优先级（选股过滤，防"低波标的穿透"）

### P1-1 选股硬性振幅下限 + 可T价差硬门槛

**改动点：**`t_build._quality_from_daily`（回测）与 `t_pool.calc_t_quality`（生产）增加**硬性振幅门槛**：
- 新增 `MIN_AMP_PCT = 3.0`、`MAX_AMP_PCT = 10.0` 门槛：近 20 日日振幅（中位数）< 3% 或 > 10% → `pass_gate=False`（硬拒）。
- 回测 `_quality_from_daily`：把 1%~3% 区间的 0 惩罚改为硬拒的 `pass_gate=False`，不再让 2% 低波标的得 0.7 分通过。
- `calc_t_quality` 已有 `spread ≤ MIN_T_SPREAD(0)` 硬拒（t_pool.py:86），但 `MIN_T_SPREAD=0.0` 过松——振幅 2% 标的价差可能刚好 >0 仍放行。建议提高 `MIN_T_SPREAD` 到 0.5（%），并要求 `spread = amp_median − 2×slippage_cost > 0.5` 才算可T价差空间。

**参数值建议：**
```
MIN_AMP_PCT = 3.0     # 近20日振幅中位下限（%），<3% 硬拒
MAX_AMP_PCT = 10.0    # 上限（%，>10% 妖票硬拒）
MIN_T_SPREAD = 0.5    # 可T价差空间下限（%），≤0.5 硬拒
```
调优后 `_quality_from_daily` 振幅贡献段改为：`3≤amp≤7 → +0.2`，`7~10 → +0.1`，否则 0 且 `pass_gate=False`。

**预期影响：** 根除 t2 P0-1 "000001、600900 这类日均振幅 2% 低波标的穿透进池"——做T对象必须具备足够日内波动，提高高抛命中概率（与 P0-1 联动）。

**风险：** 合格标的池变小（可能筛掉 601318 这种大盘银行/保险蓝筹），需确认 offer 足够；用 `cand_score_min` 与候选来源（user/pool/scan）分流，避免过度收紧人工指定标的。

**验证：** 回测确认 000001/600900 不再进入 build_decisions（decision=rejected，reason=振幅不达标）；对剩余候选跑 P0-1 高抛触发。

### P1-2 回测改用与生产一致的 `calc_t_quality`

**改动点：**`t_backtest.py:1037/1192` 的 `quality = t_build._quality_from_daily(daily_bars_t)` 改为在回测数据源可用时复用 `calc_t_quality` 的逻辑（振幅口径统一为近 6 日 m5 振幅中位 vs 生产一致）；若回测无 m5 换手率/成交额则退化为带硬振幅门槛的 `_quality_from_daily`。本质：`_quality_from_daily` 与 `calc_t_quality` 的振幅/价差口径对齐，避免"回测能过、生产拒"或反之。

**参数：** 无新增；统一口径。

**预期影响：** 消除 P0-1 回测失真，让回测选股结论可迁移到生产。

**风险：** 需保证回测数据源（m5/日线）足够，否则退化分支把低波标的都拒了会造成空池。

**验证：** 对同一历史窗口跑两种口径，对比 build_decisions 交集/差异与原因。

### P1-3 提高 `cand_score_min` + 趋势下行硬排除

**改动点：**`t_build.BUILD_PARAMS_DEFAULT`：
- `cand_score_min`: 0.55 → **0.65**。
- 在 `build_score` 增加**趋势下行硬排除**：把 `trend_gate`（MA5<MA10<MA20 + MA20 下行）从"乘性扣 0.1"升级为 `pass_gate=False`（trend_ok=False 直接拒），防止非单边但偏弱标的仍因高分进场。
- `build_score_weights` 保持 quality 权重最高（0.8），但门槛提高后 quality 维度更严格。

**参数：** `cand_score_min=0.65`；`trend_gate` 触发即 `pass_gate=False`。

**预期影响：** 与 P1-1 叠加，进场集合大幅收敛到"高波动+强趋势+流动性"的组合，减少下跌市里不断加仓吃亏损的历史覆辙。

**风险：** 过严可能导致空仓期长（机会成本）。建议分层：`user` 来源（人工指定）放宽到 0.60，`scan`/`pool` 收紧到 0.65+。

**验证：** 回测滚动建仓模式下，统计达标票数与空仓天数、累计收益 vs 原门槛。

---

## P2 优先级（条件层波动率自适应，让 high-sell/low-buy/stop 贴合真实波动）

### P2-1 做T条件波动率自适应公式

**改动点：**`t_pool.generate_conditions_for_live_pool` + `t_build.auto_gen_conditions_for_build` + `t_backtest._default_t_conditions`，用 `amp_median`（近 6 日 m5 日振幅中位）动态化（结合 P0-1 的高抛条件生成）：

```
amp_med = 近6日m5日振幅中位数(%)       # t_pool._calc_daily_amplitudes
high_sell = cost × (1 + max(1.5%, amp_med × 0.60))
low_buy    = cost × (1 − max(2.0%, amp_med × 0.60))
stop       = (实际成交/持仓成本) × (1 − 3.0%)     # 见 P2-2 绑定成交价
buy_back   = 高抛成交 × (1 − 0.4%)
```

**参数：** `amp_scale = 0.60`、`min_high_sell_pct = 1.5%`、`min_low_buy_pct = 2.0%`、`stop_pct=3.0%`。

**预期影响：** t2 P0-2：2% 振幅标的高抛阈值仍 ≥1.5%（止损下限），5%+ 标的自适应抬高到真实可及区间，提高高抛命中率；低吸不再固定 -2%（在 2% 振幅 = 等全面回撤）。

**风险：** 对高振幅标的，高抛阈值抬得太高会减少触发次数，需在回测矩阵中调 `amp_scale`（0.5/0.6/0.7）。

**验证：** 回测矩阵扫描 `amp_scale ∈ {0.5,0.6,0.7}` 下 exec 触发数与胜率。

### P2-2 止损绑定实际成交价 / 持仓成本

**改动点：**`t_pool`/`t_build` 条件生成时 `stop_loss_price` 用**当前持仓 avg_price（实际成交价）**而非 `target×0.97`（现在 t_build.py:930 用 target=0.98 的再 ×0.97，位置漂移失真）。回测端：`_default_t_conditions` 的 `stop` 基于 `init_price` 直接用 init_price×(1−3%)。

**参数：** `stop_loss_pct=3.0`（绑定成本），配底仓 -5% 清仓（P0-3）。

**预期影响：** 止损位置真实反映亏损，配合 P0-2 撮合才能真正截断深跌。

**风险：** 绑定实时 avg_price 在多次低吸成本漂移后需取加权成本（`end_of_day` 已做成本加权，生产端需读取 `paper_positions.avg_price`）。

---

## P3 优先级（AI 决策层）

### P3-1 prompt 改为盈亏比导向 + 高抛激励

**改动点：**`t_bridge.wake_agent` 的 msg（t_bridge.py:137-181）checklist 改造：
- 现有 checklist“② 亏损接近 -3% 务必保守”保留但**弱化无条件保守**；新增“③ 高抛兑现：若现价距高抛目标 ≥0.5% 且覆盖成本(滑点+手续费)，应倾向 exec 卖出兑现，而非等更低买回”。
- 加入“⑤ 盈亏比≥1.2（目标离收益空间 / 潜在回撤空间）才 exec；否则按规则兜底（P3-2）”表述。
- 把高抛（sell）触发的执行力激励写进 prompt：高抛是利润来源，不是风险动作。

**参数：** `target_edge_pct = 0.5`（现价距目标 ≥0.5% 才 exec）、`min_reward_risk = 1.2`。

**预期影响：** 减少 AI 在“薄盈余价差”上因保守框架而 wait/abandon 的占比（t2 P1-4），让高抛卖出被 AI 视为正向动作而非“卖飞风险”。

**风险：** prompt 变长可能降低解析稳定性，需同步改 P3-2 兜底。

**验证：** 对比改造前后回测 LLM 精选窗口的 `ai_exec_win_rate_pct`、`ai_abandon_count`、`realized_pnl`。

### P3-2 解析失败兜底：wait 改规则默认动作 + 打标“规则兜底”

**改动点：**`t_ai_agent._parse_ai_decision` 与 `handle_ai_decision`：
- `_parse_ai_decision` 解析失败（空/异常/非白名单 action）当前回退 `wait`（保守）→ 改为回退 `exec_based_on_rule`：按 `_rule_review`（regime 是否 HALT、连亏、无底仓、日亏预警）决定 exec/wait/abandon，并在 reason 标注“**规则兜底（解析失败）**”。
- `handle_ai_decision` 对 action 非白名单时，调 `classify_escalation`/`_rule_review` 生成默认动作，而非静默 wait。
- 审计：兜底决策在 `t_ai_actions.output` 的 reason 前缀加 `[rule_fallback]`，便于复盘归因。

**参数：** 无；逻辑变更。

**预期影响：** 消除 t2 P1-5 “解析失败一律 wait、异常一律 wait→系统向保守倾斜”的被动态；高抛触发在解析失败时按规则仍可执行（兑现），而非永远等。

**风险：** 兜底执行绕过 AI 判断，需确保 `_rule_review` 已含足够风控（regime/连亏/日亏预警）才放行 exec；建议兜底默认动作**优先 wait 仅当规则明确信号 exec**（如 HALT 时高抛 exec、CAUTIOUS 高抛 exec），低吸默认 wait。

**验证：** 单元测试：喂构造的乱码/空/非法 reply，断言返回动作与 `[rule_fallback]` 标记；回测 LLM 窗口统计兜底占比。

### P3-3 高胜率标的放开冷却/重启

**改动点：**`t_bridge` 的 `_consecutive_hits` / `hit_alert`（AI_CONSECUTIVE_HIT_ALERT=3）：
- 对 `decision_quality` exec 胜率 >55% 的标的，连续命中不强制 update_condition 冷却，允许更多次同方向触发。
- 对胜率低（<40%）的标的缩短冷却或提示减仓。

**参数：** `high_win_rearm_threshold = 55`（%）、`low_win_cool_threshold = 40`（%）。

**预期影响：** t2 P1-6/⑦：减少“高胜率持仓被过早冷却错过兑现”，让盈利标的多做几轮 T。

**风险：** 连续命中共振（反复同一方向触发）风险上升，需保证仍受单条件日触发上限（`MAX_DAILY_TRIGGERS_PER_COND=3`）与仓位上限约束。

---

## 风控汇总（叠加 P0-3）

| 风控项 | 参数值 | 落地位置 | 预期 |
|---|---|---|---|
| 底仓止损减半 | -3%（现价 vs avg_price） | t_gateway / t_monitor | 截断深跌 |
| 底仓清仓锁定 | -5% | 同上 | 防扛单 |
| 低吸加仓次数上限 | **≤2 次/日/标的**（t1 显示加仓放大亏损-6260） | t_gateway`validate_order` + t_build | 防摊薄厚亏 |
| 单标累计仓位上限 | 现 per_symbol_cap cons/std/agg=10/15/20%，**建议降为 8/12/18%** | t_build.build_sizing | 降单标集中度 |
| 日亏熔断 | 现 2% 硬闸（保留）| t_gateway | 保持 |
| 连续亏损强制放弃 | 连亏≥2 human（保留）| t_gateway.classify_escalation | 保持 |

低吸加仓次数上限是最易落地的降低加仓放大亏损手段：`validate_order` 买腿计数（daily 单标 buy）≥2 直接拒绝后续低吸。

---

## 回测验证方案

### A. rule 模式参数矩阵快跑（低风险快速对比）
用现有 `TCombinedBacktestEngine`（rule 模式，无 LLM）对 t1 四标的（000001/600036/600900/601318）+ 若干高波候选跑参数矩阵：
- **选股**：`cand_score_min ∈ {0.55,0.60,0.65}` × `MIN_AMP_PCT ∈ {2.5,3.0,3.5}`（3×3=9 组合）
- **条件**：`amp_scale ∈ {0.5,0.6,0.7}` × `min_high_sell_pct ∈ {1.5,2.0}` × `stop_pct ∈ {3.0,5.0}`（3×2×2=12 组合，选 best 选股后跑）
- 每条记录：`total_return_pct`/`win_rate_pct`/`realized_pnl`/`max_drawdown_pct`/`ai_exec_count`（sell 占比）/`trigger_count`。
- 判优：优先 `realized_pnl>0` 且 max_drawdown 不恶化；其次 sell 闭环次数。

### B. LLM 精选窗口（验证 AI 决策层改动）
对 A 中 best 组合，用 `review_fn=LLM`（真实 AI 决策）跑 2-4 个历史窗口，对比改造前后（prompt + 解析兜底 + 高抛激励）的 `ai_exec_win_rate_pct`、`ai_abandon_count`、`realized_pnl`、兜底占比。

### C. 生产灰度
- 先灰度优化后的**条件生成**（P0-1/P2-1）+ **选股门槛**（P1-1/2/3）在一个小账户（如 stock）跑 1-2 周。
- 底仓止损（P0-3）与低吸次数上限先生产开关（env flag `T_STOP_GUARD_ENABLED`）灰度。

---

## 实施顺序建议

1. **P0-1 + P0-2**：先让“高抛能触发、止损能撮合”成立（回测闭环验证）。
2. **P1-1/P1-2/P1-3**：选股过滤，防止低波标的再次穿透（可先于 1 做，因它防复发）。
3. **P2-1/P2-2**：条件波动率自适应 + 止损绑定成本。
4. **P0-3 + 低吸次数上限 + 单标上限下调**：风控加固（可与 1 并行）。
5. **P3-1/2/3**：AI prompt + 解析兜底 + 高胜率放冷。

> 建议 1-3 作为首批落地（结构+选股），4 并行，5 在回测 A/B 验证后落地。

---

## v17：AI 自主设定触发条件（AI 主导闭环收口）

> 用户需求："触发唤醒条件是 AI 设置的还是我们写死的？" → "我要 AI 自己设置条件触发，唤醒自己进行决策"。
> 本轮把条件生成从"写死的规则公式"升级为"AI 自主设定 + 系统执行命中 + 唤醒 AI 决策"的完整闭环。

### 改动点（已落地，commit fd64455）

1. **bridge 新增 `POST /conditions/generate`**（docker/dsh/bridge/lib/index.js）：
   - 入参：symbol / cost / amp_med（近6日振幅中位）/ trend / regime / context / session_id
   - Prompt 引导 AI 输出【双条件数组】：低吸（low_buy）+ 高抛回补（high_sell_then_buy_back），
     含 target_price / sell_target_price / stop_loss_price / vol_ratio_thresh / stabilize_level / reason
   - `parseConditions` 容错：无 JSON 数组 / 空数组 / 非法条件 → `source:"fallback"`（后端回退规则公式），
     价格 round2，stop_loss_price 缺失时按成本×0.97 兜底
2. **`/backtest/review` 支持 update_condition**：`parseDecision` 改为平衡括号提取 JSON
   （支持嵌套 condition 对象），动作白名单加 `update_condition`；prompt 与系统提示同步更新
3. **后端 `t_bridge.generate_conditions`**（t_bridge.py）：POST bridge 的条件生成客户端，
   带缓存（key=symbol+cost+amp_med，滚动建仓不重复唤醒 LLM）+ `AI_CONDITIONS_ENABLED` 灾难开关；
   失败返回 None → 调用方回退规则公式
4. **生产 `t_build.auto_gen_conditions_for_build`**：建仓次日条件优先 AI 生成
   （session=t-agent-{symbol}，与决策会话一致），失败回退 `build_t_conditions` 规则公式
5. **回测 `t_backtest`**：
   - `_gen_t_conditions(review_fn, symbol, price, amp_med)`：LLM 模式（review_fn 存在）→ AI 条件
     （漏 stop_loss_price 时按规则补齐）；规则模式 → 规则公式，不调桥
   - `_review` 返回 `(action, reason, cond_update)`；`update_condition` 落地
     `_apply_condition_update`（按 trigger_kind 白名单字段 patch，当日剩余 bar + 后续交易日生效，
     重新武装）
   - metrics 新增 `ai_condition_update_count`（单标的 + 组合）
6. **测试**（test_t_backtest.py +6 例）：update_condition 应用 / 缺 condition 保守等待 /
   AI 生成回退规则 / AI 结果采用并补齐止损 / 规则模式不调桥。112 passed

### 设计语义

- **条件 = AI 的"遥控器"**：AI 在建仓后自主设定触发价（低吸/高抛/止损），系统只负责
  条件命中检测 + 唤醒 + 网关风控兜底；AI 在决策时发现条件脱节可随时 `update_condition` 调整
- **规则公式是兜底不是主路径**：bridge 不可达 / LLM 解析失败 / 开关关闭时，回退
  `build_t_conditions`（振幅自适应公式），保证系统永不因 AI 故障停摆
- **回测闭环**：LLM 模式 = AI 设定条件 + AI 决策 + AI 调整条件全链路；规则模式 = 公式条件 +
  规则决策，作为对照基线

### 验证

- 服务器部署后 `/conditions/generate` 探针（成本 10.0 / 振幅 5%）→ AI 返回
  低吸 9.63（-3.7%）/ 高抛 10.38（+3.8%）/ 止损 9.5（-5%），结构合法
- 同一窗口（2026-05-18~05-29 rolling_scan）对比：#46 rule 基线 vs #47 llm AI 条件，
  对比 total_return_pct / win_rate_pct / ai_exec_win_rate_pct / ai_condition_update_count

---

## v18/v19：AI 自由跑（关拦截 + 提仓位 + 卖出后可重建仓）

> 用户发现：#47 综合收益仅 +0.83%，但 000636 +22% / 000021 +16%——"两个股票收益率很高，
> 但是综合收益率很低，是不是仓位太低了"。另发现"没有加仓动作"（低吸 0 笔成交）。

### 诊断（#46/#47 事件流证实）

1. **仓位太低**：single_order_pct=5% → 单笔 ≤1 万/标的，5 标的总建仓 4.4 万 = 净值 22%
   （total_floor_cap 55% 允许 11 万），000636 只买 300 股（4.4% 净值），涨 22% 也只贡献 1,965 元
2. **buyback 被网关拦**：CAUTIOUS 时 L1 档买腿 ≤ 可卖底仓×0.5=50 股 < 买回 100 股 →
   `买腿 100 超过档位 L1 上限 50`；日回转额超限也被拒 → 高抛卖出的筹码买不回来，
   底仓越卖越少（000636 300→0），后续无弹药
3. **低吸加仓 0 笔**：low_buy 触发 4 次全被拦——`无底仓预拦截`（卖光后）、
   `底仓浮亏 -3.9% 先减半仓`（_base_loss_guard）、`L2 禁止低吸`（近跌停）

### 改动（commit c9de456 / 9ad9a35）

1. **网关开关**（t_gateway.py，与 T_STOP_GUARD_ENABLED 同模式环境变量）：
   - `T_BUY_TIER_LIMIT_ENABLED=0`：买腿不设档位上限（返回极大值，由单笔/总仓位建议层约束）；
     无底仓低吸放行（卖出清仓后可重新建仓再 T）
   - `T_TURNOVER_LIMIT_ENABLED=0`：日回转额超限不拦
   - `T_STOP_GUARD_ENABLED=0`：关 _base_loss_guard（底仓浮亏 -3%/-5% 拦买腿）
2. **回测引擎**（t_backtest.py）：`_free_run_enabled()` 与网关开关同源；
   主循环低吸无底仓预拦截、`_rule_review` 无底仓放弃、无底仓买腿成交量
   （min 100 股新开仓）在自由跑时全部放行——T+1 账本保证当日买入次日才可卖
3. **仓位参数**（t_build.py BUILD_PARAMS_DEFAULT）：single_order_pct std 0.05→0.10、
   per_symbol_cap std 0.12→0.15（cons 0.04→0.06 / 0.08→0.10；agg 0.08→0.12 / 0.18 不变）

### 结果（#48 rule / #49 llm，同窗口）

| 指标 | #47 v17 llm | **#48 v18 rule** | **#49 v18 llm** |
|---|---|---|---|
| 综合收益 | +0.83% | **+4.95%** | **+4.77%** |
| realized_pnl | +2,529 | **+6,656** | **+7,287** |
| 拦截数 | 7 | **0** | 1 |
| buyback 买回 | 6 | **13** | **14** |
| 低吸加仓 | 0 | 0 | 0（根因已修，待 v19 验证） |
| 000636 收益 | +22.08% | **+62.63%** | **+62.52%** |

- 000636 从 +22% → +62.6%：仓位翻倍（8,880→17,760）+ buyback 闭环打通（高抛 8 次全买回，
  期末留 400 股底仓）——真正的 T+0 回转而非清仓兑现
- 拦截从 7 降到 0/1，`买回被网关拒绝`刷屏消失

### v19 预期

关 T_STOP_GUARD_ENABLED + 无底仓重建仓放行后：止损卖光/高抛卖光的标的可在低吸触发时
重新建仓（min 100 股），低吸加仓从 0 笔变为可成交，做T弹药不再被"卖光"打断。
