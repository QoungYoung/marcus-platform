# Marcus 生产全链路（狼大流程落地版 · 2026-09-10 梳理）

> 目的：把「生产实际怎么跑」与「狼大原话/规则怎么落」对齐成一份可核对的主文档。
> 来源：生产代码（/opt/marcus-platform ↔ 本仓同级路径）+ 调度配置 config/tasks.yaml + 数据库 t_* 表 + 线上产物。
> 本文只描述**已实现并在线**的链路；未实现/未接入的写在 §11。

---

## 0. 全景

```
[数据层]  gzcloud(Tushare代理) / brze(分钟) / 腾讯·新浪(实时报价) / news.db / 研报(catalyst)
   │
   ├─(收盘后 18:45) 主线确认每日更新 mainline_gate_daily
   │      concept_long → etf_flow → inst_flow → trend_confirm(结构GATE) → heat_v2(资金热度) → mainline_gate → 注入 main_line_state
   │      产物: concept_long.json / trend_confirm_<date>_long.json / heat_v2_<date>.json / mainline_gate_<date>.json / mainline_confirm_history.json
   │
   ├─(次日 08:00) 主线判定 main_line_judge  → 以最新 gate 为准覆写 main_line/candidates, 写 main_line_state.json
   ├─(08:10) 波浪判定 wave_agent → wave_state.json（level/sub_level/operation, defense/exit 硬拦不建仓）
   ├─(08:15) 板块洗盘收敛期 G3 judge
   ├─(08:20) 成分股确认 stock_confirm_judge → stock_confirm_result.json（低吸池的成分/阶段）
   │
   ├─(09:20) 主线内切换布腿器 rotation_switch_arm → 写 t_conditions(account=stock, publisher=switch)
   │      卖腿: 持仓落在拥挤/出货链 → quote.vwap_break
   │      买腿: ①当日 confirmed 主题池(pick_v2) ②rotation 链候选(再按 confirmed 主题过滤) → 每腿挂 253 + 254 两条买条件
   │
   ├─(全天, 30s/轮) TMonitor 触发 → 写 t_triggers
   │      254 = quote.dip_prev_low(当日5min最低≤前一日5min最低×1.005) ∧ vol_ratio≤0.9
   │      253 = index.m5_dump≥0.4(指数单根5min急杀) ∧ average>0 ∧ current>0
   │            【2026-09-10 更正】原写的"∧ 时段09:45-14:40 ∧ 非跌停"**不在表达式内**：
   │            09:45 下限**任何地方都没有**；14:45 禁新开在通用护栏；非跌停/跌停禁买在网关层。
   │      护栏: regime GATE / 14:45 后禁新开 / armed / 5min 冷却 / 板块权限
   │
   ├─(触发后) 执行层 t_gateway.gateway_execute → validate_order(硬闸门+账本+建议层) → 执行器成交 → paper_orders/paper_positions
   │
   └─(卖出/做T) wolf_t_rules(正T/倒T/确认制T出) + 止损扫描 + auto_exit/manual_guard 腿
   │
   └─(复盘/评估) 每日复盘 / 周五周度反思 / P2 Gate 报告 / 买点对齐评测 / 沙箱大样本回测
```

---

## 1. 运行时与调度

| 组件 | 作用 | 备注 |
|---|---|---|
| marcus-worker | APScheduler 读 `config/tasks.yaml`；**TMonitor 常驻线程**（30s/轮） | 脚本类任务在容器内以子进程运行 → **改 jobs/*.py 即时生效** |
| marcus-backend | FastAPI（:8000）+ 进程内服务模块（t_monitor / trade_graph / p2 等） | **改这些模块需重启 backend + worker** |
| marcus-postgres | 交易/条件/触发/持仓/回测表（t_conditions, t_triggers, paper_* 等） | |
| marcus-dsh | 内部 LLM 通道（:3001 /chat） | 供 agent 决策与链路抽取 |

### 1.1 关键时间表（生产实跑）

| 时间(工作日) | 任务 | 脚本 | 主要产物 |
|---|---|---|---|
| 08:00 | 主线判定 | apps/main_line/main_line_judge.py | main_line_state.json（以最新 gate 覆写） |
| 08:10 | 波浪判定 | apps/main_line/wave_agent.py | wave_state.json |
| 08:15 | 板块洗盘收敛期(G3) | jobs/sector_g3_judge.py | — |
| 08:20 | 成分股确认刷新 | apps/main_line/stock_confirm_judge.py | stock_confirm_result.json |
| 08:40 | 业绩披露日历刷新 | apps/main_line/build_earnings_calendar.py | earnings_calendar.json |
| 08:50 | 黄金坑盘前报告 | — | — |
| 09:00 / 09:10 | 盘前扫描 / 盘前诊断 | jobs/pre_market_scan.py / morning_diagnosis.py | — |
| **09:20** | **主线内切换布腿器** | jobs/rotation_switch_arm.py | t_conditions（253/254 买腿 + vwap 卖腿） |
| 09:35 / 09:53 / 10:35 / 13:35 / 14:30 | 自动交易 agent 档 | — | 建仓/减仓/只卖不买 |
| 盘中 20/31/50 分 | 盘中扫描 | jobs/market_scan.py | — |
| 每 30 分钟 | 新闻采集 | apps/news/news_collector.py | news.db |
| 15:01 / 15:05 / 15:06 / 15:20 / 15:25 / 15:30 | 净值快照 / 系统性风险 / 宏观机构状态 / P2 Gate 报告 / 今日计划 / 黄金坑快照 | — | — |
| 16:00 | 每日复盘 | jobs/daily_review_enhanced.py | — |
| 16:30 | 指数日线刷新 | jobs/refresh_index_daily.py | 指数CSV + wave_pivots |
| **18:15** | 产业链图每日增量 | apps/main_line/chain_map.py | chain_map_<date>.json |
| **18:45** | **主线确认每日更新** | apps/main_line/mainline_gate_daily.py | 见 §3 |
| 18:45 | 域外主线方向监控 | apps/main_line/scan_theme_gaps.py | — |
| 19:30 / 周五15:30 / 周日08:00 / 周六10:00 | 新闻晚报 / 周度反思 / 股票池更新 / chain_map 全量强核 | — | — |

> **关键时序（务必记住）**：主线门在**收盘后 18:45** 产出（用当日收盘数据）；因此**次日 09:20 布腿**读到的 gate 是「昨天收盘产出的那一份」。布腿选股用的日线数据也就截至**昨天**。

---

## 2. 数据层

| 源 | 用途 | 入口 |
|---|---|---|
| gzcloud（ts.gyzcloud.top，Tushare 兼容代理） | 日线 daily / daily_basic / moneyflow_dc / top_inst / margin / fund_share | wolf_confirm_pick.gz()、各 build_*.py |
| brze（tu.brze.top） | **历史分钟** stk_mins(5min/1min，可回溯 2024+)、实时 rt_k/rt_min | app/services/t_data_sources.fetch_brze_stk_mins() |
| 腾讯 qt | 实时报价（做T/触发取价） | fetch_tencent_quote() |
| 新浪 | 5min（近 ~25 交易日）、指数快照 | fetch_sina_minline() |
| 本地/DB | news.db（新闻+概念+影响级别）、stock_pool.db（成分/概念映射/市值）、concept_long.json（概念等权指数）、wave_state.json | — |

约束（实测）：brze 单次最多 8000 根（5min≈167 交易日）→ 长区间需分块；**上证指数分钟在 brze 不可用**，指数相关（253）需用 510300 等 ETF 代理。

---

## 3. 主线判定链（gate）

**脚本**：`apps/main_line/mainline_gate_daily.py`（18:45）依次执行
1. `build_concept_long.py` — 概念等权指数（20250101 起，pct 环比规避复权）
2. `build_etf_flow.py` — 主题 ETF 份额流（佐证因子）
3. `build_inst_flow.py` — 龙虎榜机构/游资净买
4. `trend_confirm.py --as-of <date>` — **结构 GATE**：逐主题判断 track_b（确认链）比例 → `gate = ratio >= 0.35`
5. `heat_v2.py --date <date>` — **资金热度**：四因子（mf5 主力5日净额 / rel 概念等权20日 / mf_accel 加速 / etf 份额）→ 排名
6. `mainline_gate.py --date <date> --fusion-json heat_v2_<date>.json` — 组合：

| verdict | 条件 | 含义（狼大语义） |
|---|---|---|
| confirmed_candidate | 热度排名 ≤ TOPN(=2) **且** 结构GATE PASS | 资金主导 + 结构确认 = 主线候选（可布腿） |
| watch | 排名 ≤ TOPN 但 GATE FAIL | 资金在、结构未确认 → 观察 |
| reserve | GATE PASS 但排名 > TOPN | 结构健康等资金点火 → 预备 |
| none | 其余 | — |

7. `mainline_state_inject.py` — 注入 main_line_state.json（Pi/agent 可见）

**波浪只作风险提示**：`wave_env`（level/sub_level/operation/c_kill）写入 gate 文件但**不否决主线资格**（狼大：调整浪内也做主线；破位位由执行层保险丝处理）。

**确认历史**：`mainline_confirm_state.ensure_history` 维护 `mainline_confirm_history.json`（"曾确认"窗口，供布腿器回退/参考）。

---

## 4. 环境/波浪链

- `wave_agent.py`（08:10，周一为主）→ `wave_state.json`：level(d1..down) / sub_level / operation(build/t_only/side/defense/exit) / confidence / reasons。
- `wave_alloc.read_wave_alloc()`：把 operation 映射成 `invest` 仓位系数；布腿器按 invest 裁剪买腿数量（build/t_only/side=1 不裁剪）。
- `trade_graph.node_check_safety_gates`：**operation ∈ (defense, exit) → 不建仓（硬拦）**；可用 `SAFETY_GATE_BYPASS` 旁路（默认关）。
  ⚠️ **范围更正（2026-09-10 核验）**：该硬拦**只覆盖 AI-agent 决策路径**（`run_trade_decision`，即 09:35/13:35 等 `auto_trade_*` 档）。
  **253/254 表达式腿路径**（`rotation_switch_arm`(09:20) → TMonitor → `t_gateway.gateway_execute`）**全程不读 `wave_state`**
  （`t_gateway.py` 只引用 `t_regime`），故不受此门约束。无底仓建仓另有一条 wave 门：`wolf_253_build.choose_intent()` 对 defense/exit 返回 `None`。
  **2026-09-10 变更**：原同处的「总回撤≥5% 禁买」「连续亏损≥3 笔当日熔断」两条账户级熔断**已删除**（审计 §5.2 S5）；
  `drawdown` / `consecutive` 仍计算并写入 state，但仅作观测，不再拦截。
- `t_regime.compute_regime()`：日内环境门（沪深300 跌 >2% → HALT；市场诊断 extreme/bear → HALT 等），产出 `gate_low_buy` / `gate_high_sell`（ALLOWED / MANUAL_ONLY / BLOCKED）。

---

## 5. 标的确认链（低吸池）

- `stock_confirm_judge.py`（08:20）→ `stock_confirm_result.json`：{子概念 → 成分股与确认阶段}。`wolf_confirm_pick.confirm_universe(theme)` 即读它。
- `chain_map.py`（18:15 增量 / 周六 全量）→ 产业链图（主题→环节→代表股），供 agent 语义核对。
- `position_judge.py`（周一）→ 概念高低位；`crowding_blacklist.json` 由基金持仓拥挤度刷新（`refresh_fund_crowding.py`）。

---

## 6. 布腿链（rotation_switch_arm，09:20）

只布腿、不下单；执行交给 TMonitor。

1. **过期**：`expire_old`（把 trade_date < today 的 switch 腿置 expired）
2. **卖侧**：当前持仓落在 `crowded_top` 或 `holdT_top`（rotation universe）→ 布 `quote.vwap_break` 卖腿（SELL_EXPR）
3. **买侧（两条路径）**
   - **路径 A**：`room + holdT` 链候选 → 该链 `pick_buy(chain, limit=3)`（按关键词匹配成分、剔 ST/拥挤黑名单、位置 LOW/MID）→ 用「当日 confirmed 主题」过滤（`theme_of_chain`）
   - **路径 B（主线确认池）**：`gate_confirmed_today(today)` 取最近 gate 文件的 confirmed_candidate 主题（排除银行）→ 逐主题 `confirm_pick(th, limit=pool_legs-got)`（内部 `pick_v2`：主题内 leader 榜 → 位置闸（距前日低 ≤5%）→ tier1 严格前 2 + tier2 接近档补位，跨主题合计 ≤ `ROT_POOL_LEGS`=4）
4. **仓位系数**：`wave_alloc.invest` < 1 时按比例裁剪买腿（保留前序=主线优先）
5. **板块权限**：`WOLF_PICK_BOARD_EXCLUDE`（默认 cyb,bj,kcb）→ 创业板/科创板/北交所腿直接剔除（账户无权限）
6. **写入**：每条买腿写两条条件 `custom_m5dump`(253) + `custom_prevlow`(254)；每条卖腿写 `custom`(vwap_break)。同时写入 **换手基准** `benchmark_turnover_profile`（近 5 完成交易日均换手）供 vol_ratio 使用。
7. `SWITCH_ARM_DRY=1` 只输出决策 JSON 不写库。

---

## 7. 触发链（TMonitor，30s/轮）

- 轮询 `t_conditions`（account=stock、当日子弹）→ 并发取价（腾讯/新浪）→ 构建字段快照 → **表达式求值**（t_expr）→ 通过后仍须过通用护栏。
- **字段与语义**
  - `quote.dip_prev_low`：当日 5min 最低 ≤ 前一交易日 5min 最低 ×1.005（狼大「挂前一天低点」）
  - `vol_ratio` = [当日累计换手% × (240/已开盘连续分钟)] ÷ base，base = 近 5 个已完成交易日**日换手均值**（`benchmark_turnover_profile.same_minute_avg`，缺省兜底 0.5%）
  - `index.m5_dump`：上证（当前以 ETF 代理）单根 5min 跌幅%，急杀阈值 0.4
  - `quote.vwap_break`：跌破日内均价线（黄线）→ 卖
- **通用护栏**：regime GATE(低吸/高卖分别) → **14:45 后禁新开仓** → `armed=1` → **同条件 5 分钟冷却** → 板块权限（`_board_tradable`）
- **状态机**：狼大表达式腿是**非消费式持续腿**（命中后保持 active，靠冷却防刷；底仓不动、T仓反复做）；其他做T腿触发即消费；manual_guard 一次性。
- **跨日结转**：`_roll_wolf_legs` 把昨日仍 active 的狼大腿复制到今日（不复活已停用/已消费的腿）。
- **其它每轮动作**：止损扫描（现价 ≤ stop_loss_price）、做T规则（正T/倒T/确认制T出）、尾盘 de-T、防御减T、board_half 等。

---

## 8. 执行链与风控（t_gateway）

触发写入 `t_triggers(pending)` 后立即走 `gateway_execute()`（唯一放行者）：

| 层 | 内容 |
|---|---|
| 账户白名单 | 仅 `stock`（狼大做T账户）可执行 |
| **硬闸门** | 裸空/无券卖、无底仓禁裸买（除非条件单建仓路径）、跌停禁买/涨停禁卖、STOP_ALL、触发事件状态异常、**wolf 回补当日上限 `WOLF_REFILL_MAX_PER_DAY`（默认 2，2026-09-10 由硬编码 1 放宽，S7）**、当日有撤销卖单需人工确认、低吸加仓次数上限（MAX_DAILY_BUY_LEGS）<br>【2026-09-10 更正】原列于此的「基础回撤 guard」**不在 `t_gateway`**（该文件对 drawdown 零引用）——回撤检查原在 `trade_graph`（agent 路径），且已随 S5 删除 |
| 账本层 | 可卖底仓断言、买腿 ≤ 可卖底仓、日亏损熔断（止损卖单豁免）。**注：原「当日回转额 ≤3×净值」上限已于 2026-09-10 删除（审计 §5.2 S5）** |
| 建议层 | **单笔 ≤ 净值 5%**、价差成本比、冷却、频次（仅告警） |
| 执行 | MarcusVNPyExecutor + PaperTradingEngine → paper_orders/paper_positions；失败/被拒 → t_triggers 置 blocked + 审计 |

---

## 9. 做T / 卖出链

- `wolf_t_rules`：正T买（`zheng_t_buy_quote`）/ 倒T卖（`dao_t_sell_quote`）/ 确认制T出（停量 + 二次不过前高）/ 日高与周期 PnL 记录；周五减 T 仓、不强制日结。
- `SELL_EXPR = quote.vwap_break`（黄线破位）用于切换链卖腿；`custom_level_sell`/`custom_vwap_sell`（manual_guard）一次性。
- **止损（2026-09-10 对齐狼大「止损六层」）**：TMonitor 每轮扫描，`止损线 → 收盘确认守卫 → 时点门 → 卖量分级`
  四道串联；止损单豁免日亏损熔断（止血优先）。
  | 层 | 狼大原话 | 系统落地 |
  |---|---|---|
  | ① 个股波段逻辑止损 | 2026-03-05「13日内跌破波段低点的-3%没有收回 直接止损」（前提「无利空」） | `wolf_early_stop.resolve_stop`：**建仓初期(≤13 交易日)** 止损线 = **建仓时点锁定的波段低点 ×(1-3%)**；`WOLF_EARLY_STOP=0` 关 |
  | ② 成趋势后→趋势线法 | 2026-03-06「成为趋势后…这个就没意义了…用**趋势线**的方法…不是一个策略用到底的」 | **语料无参数**（唯一"破5日/破趋势线"是 2022-04-26 一位用户自述，狼大未背书）→ **暂用 `stop_loss_price`**（用户 2026-09-10 决策：回测后再定口径） |
  | ③ 指数大级别止损 | 2026-08-27 549楼「**只看指数大级别**如果不走大5浪而转为下跌1浪就止损」 | `wolf_context.index_level_stop` + `t_monitor._check_index_level_stop`（只取 `wave_state.level=='down'`，带过期护栏；`WOLF_INDEX_LEVEL_STOP=0` 关） |
  | ④ 止损时点 | 2026-03-23「每天的止损**绝对不应该是下午1点到2点半**…要么早上卖要么尾盘卖」 | `t_monitor._stop_time_ok`：禁止 [13:00,14:30) 执行（该时段仅预警） |
  | ⑤ 止损预设 | 2026-08-19「按计划做 然后**设定好止损**就行了」 | 波段低点**以建仓日为锚**重算（等价建仓时锁定、不随行情滚动）；「没有收回」一半由收盘确认守卫承担 |
  | ⑥ 组合层（用仓位而非止损） | 2026-02-02「仓位一定要控制」+ 2026-04-15「70% 毫无压力」 | P2-5 `position_cap` 总仓位上限 70%（见 §8） |
  其余配套：`stop_loss_price` 由 `t_build.build_t_conditions` 写入（振幅口径，自研；非狼大口径）；
  `WOLF_STOP_CLOSE_CONFIRM`（收盘确认/假跌破）、`WOLF_BASE_EXIT_CLOSE`（收盘清仓含底仓/盘中减半）见 P2-4。
- auto_exit：自动离场腿（持续监控）。

---

## 10. 复盘 / 评估链

- 生产：每日复盘（16:00）、周度反思（周五 15:30）、P2 Gate 每日观察报告（15:20）。
- 评测：`judge_wolf_event_alignment.py`（买点与狼大事件对齐率，v3 ±5 日 92.9%）。
- **沙箱大样本回测（本轮新增，不在生产调度内）**：`/app/data/_bt_pit/`，脚本 `/app/jobs/_bt_*.py`（gate 逐日回放 → 布腿复刻 → brze 5min 触发 → 分层统计 → REPORT.md）。

---

## 11. 与狼大流程的对照

| 狼大规则/原话 | 生产落点 | 状态 |
|---|---|---|
| 只做主线（自上而下：资金热度 × 结构确认） | mainline_gate（confirmed_candidate）→ 09:20 布腿器 | ⚠️ **非唯一权威**：`rotation_switch_arm.py:300-301` 有 defensive_resource 非主线布腿分支；`auto_trade_*` 5 档走 LLM agent 不经 gate |
| 只做趋势/确定性行情，调整浪内可做 | wave_state + operation 映射 invest；defense/exit 硬拦建仓 | ⚠️ 部分：**硬拦仅在 agent 路径**（见 §4）；253/254 腿路径不读 wave。另 `t_only → invest=1.0` 不裁剪买腿 |
| 低吸：挂前一天低点、缩量才接 | 254 = dip_prev_low ∧ 0<vol_ratio≤0.9（换手节奏比） | ✅ 双条件确已实现（`rotation_switch_arm.py` `BUY_254_EXPR`）。⚠️ 原自评"已对账 09-10 实盘触发"**无法复核**（本机 PG 不可达），且当时 253 建仓分支因 P0-1 的 NameError 恒报 blocked |
| 急杀抢反弹 | 253 = index.m5_dump ≥0.4 | ⚠️ 部分：表达式仅有 `m5_dump≥0.4 ∧ average>0 ∧ current>0`；**无 09:45 下限**（现 09:30 起即可触发）。14:45 禁新开在通用护栏（`t_monitor.py:1934/1963`）、跌停在网关层，均**不在**表达式内 |
| 不买后排、龙头优先、买不到就退而求其次 | pick_v2 leader 榜 + tier1/tier2 + rank_in_concept | ✅（路径 B）。⚠️ 路径 A 的 `pick_buy` 仍走扫描序（`cands[:80]` 为 dict 序，无 leader 排序），尚未统一 |
| 买不到位置就等（空窗不硬做） | 位置闸（距前日低 ≤5%）+ 空窗等待语义 | ⚠️ 已部分修正（2026-09-10）：ETF 兜底腿（空窗必买 ETF）已默认关闭（S1）；空窗回落 legacy 语义已修（P0-4）。注：位置闸另有 tier2 8% 补位档，比表面更宽 |
| 高位不加、太高切低位 | 位置分类 position_class + 拥挤黑名单 | ⚠️ 部分（无独立"主题高度闸"，回测显示边际不显著） |
| 板块权限（无创业板/科创板权限） | WOLF_PICK_BOARD_EXCLUDE + 多层过滤（选股/布腿/TMonitor） | ✅ **已定案（2026-09-10，P2-1）**：生产**保持**该限制（账户事实，非策略选项）；**回测须开放全部权限**（`WOLF_PICK_BOARD_EXCLUDE=""`）以保证与狼大行为一致（他大量做创业板 300308/300502/300189）。<br>⚠️ 仍须知悉：该限制是"狼大一致性上限被账户权限截断"的来源；且 v2.1 位置闸的校准案例正是 300189（创业板票）→ **校准口径需与回测口径对齐后重跑**（已记入回测计划 Stage 4） |
| 底仓不动、T仓高抛低吸反复做 | 非消费式持续腿 + 5min 冷却 + 100 股底仓保护 | ✅（另：与之冲突的"底仓浮亏−3%减半/−5%清仓"守卫早已退化为放行，调用点已于 2026-09-10 移除，S6） |
| 黄线破位走人 | SELL_EXPR = vwap_break | ✅ |
| 急杀/破位时不接刀（执行层保险丝） | ~~trade_graph safety gates + gateway 硬闸门~~ | ❌ 原自评**口径混淆**：safety gates 仅在 agent 路径；且 2026-09-10 已删除总回撤/连亏熔断/日回转额（S5）、破位禁低吸④门（S2）、trail_break 移动止损（S3）。现仅余网关层硬闸门（跌停/裸空/账本） |
| 仓位/资金纪律（探仓 ≤5%） | gateway 建议层单笔 ≤净值 5% + 资金闸分批 | ✅（建议层，非硬拦）。注：日回转额 ≤3×净值 已于 2026-09-10 删除（S5） |
| ~~（补）小赚就兑现（T 目标 3-5 点）~~ | 2026-09-10 新增 `wolf_discipline.profit_take`（浮盈≥+3% 减仓锁定） | ⚠️ 已实现但**默认关闭**（`profit_take.enabled=false`），待 dry-run 后再启用 |

## 12. 已知偏差与未落地（重要）

1. **回测只覆盖布腿路径 B**（当日 confirmed 主题池）；路径 A（rotation 链候选）未建模（其输入 rotation_universe_result.json 无历史快照）。
2. **回测的布腿时序需修正**：本轮回放按「gate 当日布腿、选股截至前一日」建样本；而生产是「gate 收盘后 18:45 产出 → **次日 09:20 布腿**、选股截至 gate 当日」。两者差一天，需重跑样本（已记录）。
3. **253 的指数源澄清（2026-09-10 更正）**：本条原表述（"253 的指数用 ETF 代理"）易被误读为"生产也用 ETF 代理"。
   实际——**生产用真实上证指数**：`t_monitor._index_m5_dump()` 与 `_index_intraday_dd()` 均走腾讯
   `fetch_tencent_mkline("sh000001", freq="m5")`。**ETF 代理只是沙箱回测侧的限制**（回测走 brze，而上证指数分钟在 brze 不可用，
   故用 510300 等代理，与上证 5min 收益相关系数 0.89）。
   → 结论：253 的**生产语义与回测语义存在数据源差异**，回测结论外推时需考虑该 0.89 的相关性损失。
4. **主题成分非严格 PIT**：农业/金融/稳增长基建 用生产 stock_confirm_result；其余主题用 THEME_CONCEPTS × 概念映射近似（按市值取前 120）。
5. **成交假设**：回测按「一腿一次、每笔满额可成交」；未建模资金闸分批、涨跌停不可成交、滑点。
6. WOLF_TASKS_OVERVIEW.md §0 已列出的未做项：个股级两融/杠杆检查、板块与个股级机构行为、持仓纪律与完整复盘闭环。
7. **路径 A 的日线窗口被硬编码冻结**（2026-09-10 新发现，**待修**）：`rotation_switch_arm.py:110`（`pick_buy`）硬编码
   `end_date="20260901"`、`:193`（legacy `confirm_pick` 回退分支）硬编码 `"20260908"`。与已修的 `mainline_gate_daily.py`
   硬编码日期属同类缺陷 → 这两条路径的 `position_features` / LOW|MID 位置分类长期使用**过期收盘序列**。
8. **"曾确认 40 日窗"资格闸未接入**（2026-09-10 核实）：`mainline_confirm_state.chain_qualified` / `ts_qualified`
   有定义但**无任何调用点**；买侧实际使用 `gate_confirmed_today`（取最近一份 `mainline_gate_*.json`）。
   详见 `docs/wolf-dip-entry-rule.md`（已同步更正）。
9. **2026-09-10 删除的自造机制**（依审计 §5.2，用户拍板）：S1 ETF 兜底腿、S2 破位禁低吸④门、S3 trail_break 移动止损、
   S4① 60分MA 缺数据硬禁建仓、S5 账户级熔断三件套（总回撤/连亏/回转额）、S6 底仓浮亏守卫调用点；
   S7 回补口径已统一为 `WOLF_REFILL_MAX_PER_DAY`（默认 2）。对应 commit：566a410 / b26878a / 82a486f / ebde502。
   **保留**的同类机制：S8 板上减半（经复核**有狼大原话支撑**：2026-09-01 楼678「吃一口减一半」「板上减了」）、
   S4② 日内分位硬禁（机制=不追高，与狼大一致）、S5 之外的网关层硬闸门、S10 P2 宏观开关。
10. **253/254 建仓链的 P0-1 缺陷已修**（2026-09-10）：`wolf_253_build.build_253` 成功分支原引用未定义的 `snapshot`
    → 成交后抛 NameError 被 except 吞掉、上报 blocked、`mark_base_254` 永不调用 → 254「3 日内 ≤2 次回补」链整条失效。
    已修（commit b1b5c20）。**此前任何基于 `t_triggers.status` 的"建仓成功率"统计均不可信**。
11. **止损六层落地的两处"已知未定"**（2026-09-10）：
    (a) **层②趋势线法没有口径** —— 语料里狼大只说"用趋势线的方法"，**没给任何参数**；
        唯一出现的"破5日减仓/破趋势线止损"出自 2022-04-26 **一位用户自述**，狼大未背书。
        用户决策：**趋势中段暂用既有 `stop_loss_price`**（振幅口径，`avg×(1-max(3%, 振幅×0.55))`），
        等回测（`docs/backtest-plan.md`）出结果再定趋势线口径。落地位置 = `wolf_early_stop.resolve_stop` 的
        非建仓初期分支。
    (b) **层①的"波段低点"窗口语料未给** —— 狼大只说"波段低点"，未给回看窗口。
        本仓取 `WOLF_SWING_LOW_WIN=13`（与"13日内"同数），**可回测调参**；`WOLF_EARLY_STOP_PCT=3` 为原话数字。
        注：该止损线在多数情形下与既有振幅止损**量级接近**（253/249 属回踩低吸，建仓日常在近 13 日低点附近），
        但方向上**替换**而非叠加 —— 狼大「不是一个策略用到底的」，把两条线做 min/max 复合属自造。
    (c) 层①的前提「无利空」**未落代码**：本仓没有可靠的个股利空数据源，硬加一个"利空"维度即自造，
        故只登记为已知缺口（狼大的意思是在**没有利空**的情况下也照样执行该止损）。
12. **U9 主题容量约束 + S9 兑现幅度（2026-09-11 落地，阈值待回测校准）**：
    (a) **U9**：狼大 2026-09-02 楼729「小票就太多了 不好判断」+ 楼733「农业拉10个点带动的资金量不过100E」。
        此前 `capacity_amt20_yi` **只输出提示、不拦截**。用户决策用**相对分位而非绝对名额**
        → `wolf_confirm_pick.theme_quantile_keep`：主题内 `leader` 分位前 `WOLF_THEME_QUANTILE_PCT`%（默认 50）才可买，
        路径A（`rotation_switch_arm.pick_buy`）与路径B（`pick_v2`）**共用同一函数同一 env**（不再分两套口径）。
        **阈值语料没给数**（只有"小票太多了"这个定性说法）→ 水平由回测校准（`docs/backtest-plan.md` A16），`=0` 关闭。
        过滤点在 `theme_r20`（主题均值）算完之后 —— 否则会污染 rs 闸的基准，等于连带改掉另一条已验证的门。
        边界口径：取 `ceil(n×pct%)` 名，**与第 k 名并列的一并保留**（不按序位切并列）。
    (b) **S9**：`roundtrip_sell.ROUNDTRIP_SELL_UP` 由 **0.008 → 0.03**。
        狼大 2026-08-13 楼275「至少能有吃 **3-5个点** 的幅度吧 哪怕是ETF」、楼280「等下一个 3-5个点 的机会」；
        2026-09-02 楼728「半导体只要 3个点 就远远超过这个量了」。原 +0.8% 属**自设的小止盈**，
        连狼大说的"波动连手续费都不够"那档都不到。可 `WOLF_ROUNDTRIP_SELL_UP` 覆盖（0.05 取上沿）。

---

## 13. 运维要点

- **改脚本类任务（jobs/*.py、apps/*）**：即时生效（子进程执行）。
- **改 backend 进程内模块（t_monitor/trade_graph/p2 等）**：必须重启 backend + worker。
- **改调度**：config/tasks.yaml（worker 需 reload/重启）。
- **回放纪律**：任何 DATA_DIR 沙箱回放，先把「本次会写的文件」列出并 COPY 到沙箱（严禁软链可写文件）；分片并行上限 2（4 分片会被 OOM 静默杀掉）。
