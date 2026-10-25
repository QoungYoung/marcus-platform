# 防御逻辑现状审计：分散在哪、为什么打架、怎么收口

> 背景：用户问「防御策略是不是分散在各处了？做浪型、主线、止损、B反 时都涉及防御」。
> 结论先说：**是，而且不是简单的重复，是三种分裂——概念分裂、数字分裂、执行分裂。**
> 但也不建议把所有模块合成一个巨无霸：**信号层可以分散，仲裁层必须唯一，执行层必须读同一个状态。**

---

## 0. 一句话诊断

现在的系统里，“防御”至少被当成六种东西：

1. **不建仓**（wave operation=defense / p2 gate）
2. **降仓**（position_cap / position_tier / t_derisk）
3. **减 T**（t_monitor defensive_t_reduce）
4. **止损/破位**（wolf_stop_layer / break_policy）
5. **观望/不做**（B反、主线、prompt 的 “看不清”）
6. **清仓**（break_policy down_default=0 / 狼大 “下跌趋势就不做”）

六个含义，各自有各自的阈值和动作，没有一个地方能回答：**“今天到底是什么防御档位、总仓位上限是多少、该减谁？”**

---

## 1. 现状地图（按层，不按文件）

### 1.1 信号层（判断“现在是什么环境”）

| 模块 | 它输出的“防御” | 输出对象 | 状态 |
|---|---|---|---|
| `apps/main_line/wave_agent.py` | `operation=defense/exit` | `wave_state` | 生产（调度） |
| `backend/app/services/wave_gate.py` | 买类腿拦截（t_only/defense/exit） | `check_gate()` | 生产 |
| `apps/main_line/systemic_risk.py` | level 1=防御 / level 2=彻底止盈 | `systemic_risk.json` | 生产（15:05） |
| `apps/main_line/build_macro_state.py` | `margin_burst` / `gjd_withdraw` 等 flags | `macro_state.json` | 生产（15:06） |
| `backend/app/services/wolf_top_signals.py` | 顶部阶段（≥2 条） | 给 `wolf_boll_levels` | 生产 |
| `backend/app/services/t_derisk.py` | 反抽失败→降到 40%；高标杀跌 | `stats()` | **仅测试，未接线** |
| `backend/app/services/t_chop_guard.py` | 阴跌中段→仓位区间 60-70/50 | 加仓闸 | 生产（默认关） |
| `apps/main_line/risk_gate.py` | 两融分位≥0.8+顶部→reduce | `decide()` | 生产（trade_graph 软约束） |
| `apps/main_line/rotation_gate.py` | defense/exit→block 或主线内轮动 | verdict | 生产 |
| `jobs/wolf_brebound_1450.py` | 只给事实，不给结论 | 文本底稿 | 生产（观察） |
| `backend/app/services/wolf_mainline_select.py` | 主线/方向 | `main_line_state` | 生产 |

### 1.2 仓位层（决定“最多多少钱”）

| 模块 | 口径 | 数值 | 状态 |
|---|---|---|---|
| `wolf_discipline.position_cap` | 按 operation 分档 | build 75 / t_only 50 / side 50 / defense 30 / **exit 50** | 生产（提示层） |
| `position_tier.three_tier_gate` | 按 operation 分桶 + cash floor | defense cash 70 / **exit cash 50**（=100-target） | 生产（硬拦） |
| `wolf_pick/card.py` | `_OP_PLAN` | build 75 / t_only **70** / side 50 / defense 30 / exit 30 | **仅测试，未接线** |
| `prompt_seeds.py` | Pi stance 表 | green 60 / yellow 50 / red 0；cash 25 | 生产（提示词） |
| `candidate_pool_monitor.py` | `position_limit` 默认 60 | 60 | 生产 |
| `trade_graph.py:872` | TOP3 权重建议 | defense 「仅30%仓参与」/ **exit「70%仓(只降不空)」** | 生产（提示词） |
| `risk_manager.py` | 连续 NAV 缩放 | 1.0→0.2，永不清仓 | **仅模型重训用，不用于仓位** |

### 1.3 入场层（“能不能买”）

| 模块 | 判据 | 动作 |
|---|---|---|
| `p2_entry_gate.py` | wave defense/exit、systemic≥2、macro flags | hard block / multiplier |
| `wave_gate.check_gate` | t_only/defense/exit 买类拦截 | block/降档 |
| `position_tier.three_tier_gate` | operation + intent | intent_allowed / cap / cash floor |
| `check_entry_filters`（`api/indicator.py`） | 汇总 p2 + P3 + 技术/资金/超买 | hard_block |
| `rotation_gate.py` | defense/exit 时是否允许主线内切 | verdict |

### 1.4 离场层（“该减谁”）

| 模块 | 触发 | 动作 | 状态 |
|---|---|---|---|
| `wolf_stop_layer.evaluate` | down-wave / 关键位破位 / 反抽失败 / 目标位 | reduce_to / no_new / alert | 生产（v3，`WOLF_V3_LIVE` 默认关） |
| `break_policy.decide` | B0-B4 破位严重度 | cap 50/30/0-30 | 生产（break_stop） |
| `break_stop_service` | 指数/板块/个股破位 | 落库 + QQ（alert 模式） | 生产 |
| `t_monitor._check_defensive_t_reduce` | t_only/side/defense + 量能不足/滞涨 | 减 T | 生产 |
| `t_monitor._check_stop_loss_v3` | 调 wolf_stop_layer | 计划/（live 才卖） | 生产（灰度） |
| `t_monitor` 其他腿 | 板上减半、小赚兑现、BOLL、逻辑时间止损、周末避险 | 卖 | 生产 |
| `wolf_exit_rules` | 浮亏+破 MA5 | 放行卖底仓 | 默认关 |
| `t_trend_break` | 趋势突破短线 | +5%/+8%/-5%/5 日 | 默认关 |
| `t_protect` / `t_capacity` / `wolf_base_hold` | 卖腿分类/底仓/T 仓 | 执行约束 | 生产 |

### 1.5 执行/反馈层

`t_gateway`（下单/时点门）、`decision_window`（14:30/14:50/14:55/15:00）、`alert_hub`/QQ、`daily_decision`、`daily_strategy_summary`、`backtest_monitors`。

### 1.6 关键事实：有些“防御”根本没接线

| 模块 | 证据 | 含义 |
|---|---|---|
| `t_derisk.py` | 只在 `backend/tests/test_derisk.py` 被 import | 写了一个“反抽失败降到 40%”的模块，生产没接 |
| `wolf_pick/card.py` | 只在 `backend/tests/test_wolf_pick.py` 被 import | 一套 `_OP_PLAN` 仓位表，生产没接 |
| `risk_manager.py` | 只在 `model_update_scheduler.py` 被 import | 连续降仓曲线不参与实际仓位 |
| `wolf_stop_layer` live | `WOLF_V3_LIVE` 默认 `0` | 止损层默认只记录/计划，不自动卖 |

> 这比“重复”更危险：**看起来有防御，实际没生效**。做审计时不能只看文件存在，要看调用链。

---

## 2. 三种分裂

> 审计快照说明：下表的 exit 70/50/30/0 是**审计当时**的状态；2026-10-25 已拍板统一为 **G3=15%**（见 §7），代码/配置已按此修改。

### 2.1 概念分裂：同一个词，六种动作

| 模块 | “防御” = | 实际动作 |
|---|---|---|
| `wave_gate` | 防御不建仓 | 拦买类腿 |
| `position_cap` | 有风险 30% | 建议总仓 ≤30% |
| `position_tier` | defense 档 | new_base 禁，add_base 允许 3% |
| `t_monitor` | 防御性减 T | 卖 T 仓 |
| `wolf_stop_layer` | down-wave | 总仓 30% cap |
| `break_policy` | B3-3 down-wave | cap 0-30，默认 0 |
| `B反` | 看不清/不做 | 只写文本，不改仓位 |

**后果**：同一天可以出现“wave=defense 禁新开 + position_tier 允许加 3% + t_monitor 减 T + stop_layer 30% cap”，四个动作互不协调。

### 2.2 数字分裂（最危险）

| 概念 | 版本 A | 版本 B | 版本 C | 版本 D | 语料口径 |
|---|---|---|---|---|---|
| **exit / 下跌趋势** | `trade_graph:872` **70%** | `wolf_discipline` **50%** | `wolf_stop_layer:151` / `wolf_pick:38` **30%** | `break_policy:33` **0%** | 狼大「下跌趋势就不做」→ **0-20%** |
| **defense / 有风险** | `wolf_discipline` 30% | `position_tier` 允许 add 3% | `trade_graph:872` 「30% 仓参与」 | — | 「有风险就 30%」 |
| **t_only / 调整** | `wolf_discipline` 50% | `wolf_pick` **70%** | prompt yellow 50% | — | 「调整就 50%」 |
| **现金底线** | prompt **25%** | 语料桶级 **20%** | G2 **70%** | G3 **80-90%** | 50/30/20 桶级 |
| **账户回撤** | prompt **5% 硬禁止** | `risk_manager` 2-8% 连续 | 方案 8%/12% | `trade_graph:1408` 「已删除」 | 狼大无固定百分比 |
| **周末避险** | `weekend_de_risk` **50%** | `wolf_weekend_hedge` **65%** | — | — | 2026-08-21「65% 过周末」 |
| **指数破位 cap** | `break_policy` 50% | 30% | 0-30% | `t_derisk` **40%** | 50/30/0-30 有语料，40 是代理 |
| **两融阈值** | `risk_gate` **0.8 分位** | 方案 **0.9 分位** | — | — | 两融高位+查杠杆 |

> 最刺眼的是 **exit 的 70/50/30/0 四个版本**。同一句“下跌趋势就不做”，系统里能读出四种仓位。

### 2.3 执行分裂：同一条规则，四种落地方式

| 落地方式 | 模块 | 后果 |
|---|---|---|
| 硬拦买 | `p2_entry_gate` / `wave_gate` | 买不进，但可以加已有仓 |
| 软提示 | `prompt_seeds` / `trade_graph` | LLM 可能不听 |
| 只告警 | `break_stop_service` / `wolf_stop_layer` 默认 | 看到了但不卖 |
| 自动卖 | `t_monitor` 部分腿 / `WOLF_V3_LIVE=1` | 真卖，但可能和上面三层冲突 |

**典型冲突链**：

```
wave=exit（禁新开）
  → p2_entry_gate 硬拦新开 ✓
  → position_tier 仍允许 add_base 3% ✗
  → position_cap 说总仓 ≤50% ✗
  → trade_graph 提示「exit→70%仓(只降不空)」✗
  → wolf_stop_layer 说 down-wave 30% cap ✗
  → break_policy 说 down 默认 0 ✗
```

结果：**“下跌趋势”里，系统同时认为应该 0%、30%、50%、70%。** 谁最后执行，取决于哪条链路先跑到。

---

## 3. 为什么会变成这样（根因）

1. **每个功能自带风控**：做浪型时加一个 operation，做主线时加一个 rotation verdict，做止损时加一个 stop layer，做 B反时加一个观察结论。每个都是合理的局部最优。
2. **没有“防御状态”的 schema**：大家用形容词（defense/risk/red/不做），没有统一的状态对象（level/target/bucket/reduce_order）。
3. **配置分散在四层**：代码默认值、`config/*.json`、DB（`wolf_discipline_config`）、prompt 文本。改一处，另外三处不知道。
4. **语料引用日期不同**：50% 来自 “调整就 50%”，30% 来自 “有风险就 30%”，65% 来自 “65% 过周末”，40% 来自某天的反抽失败案例。每个数字都有出处，但**不是同一个口径**。
5. **没有跨模块一致性测试**：单测各自过，没人测“同一天四个模块是否给出同一个仓位”。
6. **部分模块写完没接线**：`t_derisk`、`wolf_pick/card`、`risk_manager` 的仓位部分。看起来有防御，实际没有。

---

## 4. 目标架构：信号可以分散，仲裁必须唯一

```
┌──────────────────────────── 信号层（只报信号，不写仓位）────────────────────────────┐
│ wave_agent  mainline  systemic_risk  macro_state  wolf_top_signals  t_chop_guard  │
│ risk_gate   t_derisk  B-rebound  break_detector  margin/ETF/北向 ...              │
└───────────────────────────────────┬───────────────────────────────────────────────┘
                                    ↓  DefenseSignal{name, severity, level_hint, asof, reason}
┌──────────────────────────── 仲裁层（唯一写状态）──────────────────────────────────┐
│ defense_cash.arbitrate(signals) → defense_state{level, target, buckets, reasons}   │
│ 规则：取最严 / 段位闸门 / 陈旧信号忽略 / 人工只能更保守 / 不取平均                  │
└───────────────────────────────────┬───────────────────────────────────────────────┘
                                    ↓  只读
┌──────────────────────────── 执行层（只读状态，不自己判防御）──────────────────────┐
│ p2_entry_gate  wave_gate  position_tier  wolf_stop_layer  t_monitor  t_gateway      │
│ prompt_seeds（渲染状态）  trade_graph（注入状态）  QQ/告警                          │
└────────────────────────────────────────────────────────────────────────────────────┘
```

### 4.1 唯一状态对象

```yaml
defense_state:
  asof: "2026-10-16T14:50:00+08:00"
  mode: intraday|after_hours|replay
  level: G0|G1|G2|G3
  segment: 4-3|4-4|4-5|down1|undecided
  target_pct: 30
  buckets: {base: 25, t: 5, cash: 70}
  hard_triggers: [...]
  soft_triggers: [...]
  reduce_order: [...]
  keep: [...]
  invalidation: "收盘站回 3764"
  source: program|agent|human
  stable_key: "20261016:G2:hash"
```

### 4.2 仲裁规则

1. **取最严，不取平均**：G3 > G2 > G1 > G0。
2. **段位闸门**：4-3 筑底 + 无 G3 硬触发 → 封顶 G1。
3. **陈旧信号忽略**：`asof` 超过 N 天/不是当日 → 不参与（不是 fail-open）。
4. **人工只能更保守**：可以手动升档，不能手动降档绕过硬触发；覆盖需理由 + 过期时间。
5. **任何模块不能把另一个模块的档位调低**。

### 4.3 模块契约

| 模块 | 现在 | 目标角色 | 动作 |
|---|---|---|---|
| `wave_agent` | 直接给 operation | **信号提供者** | operation 仍可作 level_hint，但不再直接等于仓位 |
| `wave_gate` | 自己判 defense/exit | **入场适配器** | 读 `defense_state`，保留签名兼容 |
| `p2_entry_gate` | 自己判 wave/systemic/macro | **入场适配器** | 读状态；hard block 由级别决定 |
| `position_tier` | 自己算 cash floor | **intent 适配器** | cash floor 从 `defense_state` 来 |
| `wolf_discipline.position_cap` | 自己分档 | **适配器/提示** | 读状态；不再维护 exit=50 |
| `wolf_pick/card._OP_PLAN` | 未接线 | **删除** | 用 `defense_state` |
| `risk_manager` | 连续缩放（未用于仓位） | **退役或转账户回撤信号** | 不参与总仓 |
| `systemic_risk` | level 1/2 | **信号** | 只报 level，不写仓位 |
| `risk_gate` | R-R3 reduce | **信号** | 只报“两融高位+顶部”，不直接 reduce |
| `rotation_gate` | defense/exit block/轮动 | **信号 + 例外申请** | 例外必须受总 cap 约束 |
| `wolf_stop_layer` | down-wave 30% cap | **离场适配器** | cap/target 从状态来 |
| `break_policy` | 自带 50/30/0-30 | **severity 计算器** | 保留 B0-B4，删除 cap |
| `t_derisk` | cut_to 40 | **信号**（未接线） | 先接线为信号，删除固定 40 |
| `t_chop_guard` | 仓位区间 60-70/50 | **信号** | 只报“震荡/阴跌”，不报目标仓 |
| `wolf_top_signals` | 顶部阶段 | **信号** | 只报顶部强度 |
| `wolf_weekend_hedge` | 65% 过周末 | **overlay 信号** | 与 `weekend_de_risk` 合并成一个 |
| `t_monitor` | 多条卖腿 | **离场执行器** | 读状态；卖腿只负责“怎么卖” |
| `prompt_seeds` | 自带 stance 表 | **渲染器** | 把 `defense_state` 渲染进 prompt，删独立仓位表 |
| `trade_graph` | exit 70% / defense 30% | **注入器** | 只注入状态，不再给数字 |
| `B-rebound` | 观察 | **观察信号** | 输出“防御倾向”作为 soft signal，不改仓位 |
| `daily_decision`/`summary` | 各写各的 | **报告** | 只展示 `defense_state` |

---

## 5. 迁移顺序（先止血，再收口）

### P0：先修会导致“错误暴露”的数字（1-2 天）

| 优先级 | 问题 | 修法 |
|---|---|---|
| P0-1 | `exit=50` / `trade_graph exit=70` | 统一为 **0-20%**（底仓 10-15 + T 5） |
| P0-2 | `wolf_stop_layer` down-wave 30% | 改为读 `defense_state`，不写死 30 |
| P0-3 | `position_tier` defense 允许 add 3% | G2 默认禁 add；如保留，必须标“例外”并受 cap |
| P0-4 | `weekend_de_risk` 50 vs `wolf_weekend_hedge` 65 | 合成一条：缩量+不拉升 → 65%；否则 50% |
| P0-5 | prompt 5% 回撤硬禁止 vs 8/12 | 统一账户回撤阶梯：5% G1 / 8% G2 / 12% G3 |
| P0-6 | `t_derisk` / `wolf_pick.card` / `risk_manager` 未接线 | 标注 deprecated 或接线为信号，不能“看起来有” |

### P1：影子仲裁（2 周）

- 新增 `defense_cash.py`，只读现有信号，计算 `defense_state`。
- **不改执行**：现有模块照旧，`defense_state` 只写库/推送。
- 每天对比：`defense_state` vs 实际动作，记录分歧。
- 目标：找出所有“状态说 G2、执行却在加仓”的链路。

### P2：适配器切换（1-2 周）

- `p2_entry_gate` / `position_tier` / `wolf_stop_layer` / `t_monitor` 改为读 `defense_state`。
- 旧逻辑保留为 fallback，但只在状态缺失时用。
- 分歧看板：同一 fixture 下，旧逻辑 vs 新状态必须一致，或解释差异。

### P3：删除重复（1 周）

- 删除 `_OP_PLAN`、`exit=50`、`cut_to=40`、`down-wave=30`、prompt stance 表等重复口径。
- 保留信号提供者 + 执行适配器。
- 加 CI：同一组 fixture 下，所有模块必须给出同一个 `level/target`。

### P4：自动减仓（可选）

- 只在 G3 自动减 T/高位；底仓减仓仍需人工确认。
- 保留 kill switch。

---

## 6. 防复发规则（比代码更重要）

1. **新增防御规则必须是“信号”，不能直接写仓位**。信号 schema：`{name, severity, level_hint, asof, reason, invalidation}`。
2. **`defense_state` 只有一个 writer**：`defense_cash`。其他模块只能读。
3. **一个配置文件**：`config/defense_cash.json`。其他 json 里的仓位数字标记为 derived/read-only。
4. **每个阈值必须有出处**：`corpus_ref`（日期+原话）或 `engineering` 标签；没有出处不许上线。
5. **跨模块一致性测试**：G0/G1/G2/G3 四个 fixture，断言所有适配器给出的 target 一致。
6. **每日分歧报告**：`defense_state` vs 实际动作，分歧 >0 就告警。
7. **PR checklist**：这条规则会不会和 wave/position/stop/B反 任一口径冲突？冲突时谁是 SSOT？

---

## 7. 已拍板（2026-10-25）

| # | 问题 | 决定 |
|---|---|---|
| 1 | G3 目标仓位 | **15% = 底仓 10 + T 5** |
| 2 | G2 是否允许核心底仓 add ≤3% | **默认禁止**；只在「4-3 支撑 + 无硬触发」时人工例外，需记录理由与过期时间 |
| 3 | 周末避险目标 | **65%**，但只在「缩量 + 不拉升」时；其余情况仍按 G1/G2 |
| 4 | Pi stance 表 / risk_manager | **退役**：仓位统一到 G0-G3；`risk_manager` 不再参与防御/仓位，只保留模型重训的 emergency_check（后续拆出） |

**落地含义**

- `exit` 档从 70/50/30 三个版本统一为 **15%**；现金底线 85%。
- `defense` 档保持 **30%**；`new_base` 与 `add_base` 默认都禁，人工例外单独留痕。
- 周末：`wolf_weekend_hedge` 的 65% 成为唯一目标；`weekend_de_risk` 的 50% 只作为“未满足缩量+不拉升”时的保守回退。
- prompt 里的 green/yellow/red 只保留为兼容别名：green→G0、yellow→G1、red→G2；G3 无旧别名。

---

## 8. 一句话总结

**防御不是每个模块各自的态度，而应该是一个唯一的状态。**
信号层可以分散（浪型、主线、止损、B反 本来就该各看各的），但**仲裁层必须唯一，执行层必须读同一个状态**。
现在最大的风险不是“没有防御”，而是“有四种防御，互相不知道”——尤其 `exit` 的 70/50/30/0。
