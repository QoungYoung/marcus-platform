# 全拟真回测 · 每日执行流程（2026-01-01 起，空仓 + 固定初始资金）

> 目的：把**生产每个交易日实际发生的事**按同一顺序、同一口径重放一遍，用来回答"这一年按生产口径会怎么买卖"。
> 本文是**待你检查的流程稿**，不是已经跑完的结果。标注含义：
> **R** = 直接复用生产代码（同一模块，只把数据截到 as-of）；**S** = 需要加 as-of 打桩/改造；
> **M** = 分钟级撮合（回测新增，生产是 30s 轮询实时源）；**A** = 依赖 LLM/agent 的语义产物（近似或跳过，必须标注）。
>
> 起点：`stock` 账户 **25 万**（= 生产 `paper_account_info.seed_initial_capital`），**空仓**；`t`（做T）与 `golden_pit`（DCA）账户**默认不纳入**（见 §7 待拍板）。
> 交易标的：生产同一执行域（剔除创业板/科创板/北交所 = `WOLF_PICK_BOARD_EXCLUDE`），整手 100 股，T+1。

---

## 0. 生产时间轴 ↔ 回测步骤（总表）

| 生产时刻(cron, Asia/Shanghai) | 生产任务 | 回测里做什么 | 标记 |
|---|---|---|---|
| T-1 18:15 | `chain_map.py`（产业链图 v3 + AI 裁决） | 生成 T-1 的产业链图（AI 部分 A） | S/A |
| T-1 18:40 | `wolf_limit_ladder_scan.py`（涨停梯队） | 同代码重放（输入=当日涨跌停明细） | R |
| T-1 18:45 | `daily_inputs_chain.py`（**盘后行情输入链**：concept_long → etf_flow → inst_flow → trend_confirm → heat_v2 → mainline_gate） | 同代码逐日重放（已有 `jobs/stage0_replay_v2.py` 先例；产物落沙箱） | R |
| T-1 18:45 | `scan_theme_gaps.py`（域外主线） | 同代码重放 | R |
| T-1 18:50 | `wolf_theme_resilience.py`（C2 跌得少弹得早） | 同代码重放 | R |
| T-1 18:52 | `wolf_volume_gate.py`（G10 量能门槛） | 同代码重放 | R |
| T-1 18:55 | `wolf_mainline_select.py`（D1 方向层主线选择） | 同代码重放 → 次日的「方向层池」 | R |
| T-1 19:40 | `wolf_review_score.py`（复盘打分） | 不需要（只影响报告） | — |
| T-1 19:45 | `daily_decision.py`（G1 决策对象） | **A**：LLM 生成；回测**不参与下单**，仅记录 | A |
| T-1 19:50 | `daily_archive.py`（G2 存档） | 不需要（回测自己落盘） | — |
| T 08:00/08:05 | `stock_pool_manager` / `arkvol_checkin` | 交易日历 + 股票池快照（从 DB/relay 取） | R |
| T 08:05 | `derive_sub_universe.py`（主线子方向细分刷新） | 同代码 + **as-of 打桩**（脚本无 `--date`） | S |
| T 08:10 | `wave_agent.py`（波浪判定，**LLM**） | **纳入**（用户拍板）：同代码 + as-of 钉住 + LLM 录制/回放 | **S/R** |
| T 08:15 | `sector_g3_judge.py`（板块洗盘收敛期） | 同代码重放 | R |
| T 08:18 | `tranche_ladder_report.py`（试仓档盘前报告） | 同代码重放（读 `stock_confirm_result`） | R |
| T 08:20 | `stock_confirm_judge.py`（**成分股确认刷新**） | 同代码 + as-of；其中 AI 裁决部分 **A** | S/A |
| T 08:25 | `daily_strategy_summary.py` / `daily_decision.py --premarket` | 仅记录 | A |
| T 08:40 | `build_earnings_calendar.py` | 同代码（业绩门槛用） | R |
| T 09:00 / 09:10 | `pre_market_scan.py` / `morning_diagnosis.py` | 同代码（盘前扫描/诊断只读） | R |
| **T 09:20** | **`rotation_switch_arm.py`（主线内切换布腿器）** | **核心步骤**：`jobs/replay_entry_0920.py` 已实现"切点= T-1"的逐日重放（含主题可买门 → 路径A/B 选股 → v3 排序 → 板块前过滤 → 两道入口门 → 253/254 腿 + 均线挂单价） | R |
| T 09:35 / 09:53 / 10:35 / 13:35 / 14:30 | 自动交易（Pi agent 5 个窗口） | **不纳入**（用户拍板）；只做"生产确实发生了"的记录 | A |
| T 09:50 / 14:10 | `rotation_switch_agent.py`（**主线内切换·交易腿 agent**，LLM 决策 + 直接下单） | **纳入**（用户拍板）：同代码 + as-of 钉住 + LLM 录制/回放 + `/trades` 改走回测撮合；生产当前 OFF → 回测跑 **on/off 两版**对照（见 §2.1） | **S/M/A** |
| T 09:36 / 14:44 | 黄金坑 DCA 定投 | **不纳入**（用户拍板，另一账户） | A |
| T 14:31 | `wolf_weekend_hedge.py`（G9 周末避险） | 同代码重放（周五/长假前） | R |
| **T 09:30–15:00** | `t_monitor`（**30s 轮询**）+ `t_gateway`（三阶校验）+ 纸交易撮合 | **M**：用分钟线逐 bar 重放（`index.m5_dump` / `quote.dip_prev_low` / `vwap_break` / 量比 / 分时均价 / 止损）；撮合按 §3 规则 | M |
| T 15:01 | `snapshot_portfolio.py`（净值快照） | 回测自算净值（同一口径） | R |
| T 15:05 / 15:06 / 15:20 / 15:25 | 系统性风险 / 宏观机构 / P2 gate 报告 / 今日计划 | 同代码重放（风险开关是次日闸门输入） | R |
| T 15:30 | 黄金坑日快照（`golden_pit` 账户） | 不纳入 | — |
| T 16:00 | `daily_review_enhanced.py` | 仅记录 | — |
| **T 16:30** | `refresh_index_daily.py`（指数日线 + `mkt_bars.ensure_fresh`） | 同代码（给次日 t_monitor 的支撑/前收口径） | R |
| 盘中 `*/30 8-16` | `wolf_neg_event_scan.py`（无利空公告标记） | 同代码重放（数据受限时标注） | S |
| 盘中 `20,31,50 9-11,13-14` | `market_scan.py`（盘中扫描） | 只读，可跳过 | — |
| 盘中 `*/5 9-11,13-14` | `record_orderbook.py`（盘口五档） | **无法回放**（无历史盘口）→ 相关守卫按"不适用"处理并标注 | — |

---

## 1. 一天的完整时序（回测视角）

```
T-1 盘后（已由 stage0 链生成，落沙箱 data/_bt_runs/<T-1>/）
  18:45  concept_long / etf_share_flow / theme_inst_flow → trend_confirm → heat_v2 → mainline_gate
  18:50  theme_resilience(C2)   18:52 volume_gate(G10)   18:55 mainline_select(D1)
  ⇒ 产物：T-1 的主题确认集、方向层池、结构/资金/量能状态

T 盘前（as-of 只用 ≤ T-1 的数据）
  08:05  derive_sub_universe → rotation_sub_universe / rotation_universe_result
  08:10  wave_agent → wave_state（浪型闸门）
  08:15  sector_g3 → 板块洗盘收敛期
  08:18  tranche_ladder_report（试仓档）
  08:20  stock_confirm_judge → stock_confirm_result（选票确认域）
  08:20  position_class（概念高低位；周一）→ position_class_result
  08:40  earnings calendar（业绩门槛）

  09:20  ★ rotation_switch_arm（布腿）
         ① 卖侧：持仓落在「拥挤无空间/出货链」→ vwap_break 卖腿
         ② 买侧：方向层池 → 主题可买门(结构 ∧ 资金) → 路径A(pick_buy) / 路径B(pick_v2, v3 排序)
                 → 板块前过滤 → **两道入口门**(WOLF_CLOSE_POS_GATE / WOLF_DEFENSIVE_GATE)
                 → 每条腿布 253(custom_m5dump) + 254(custom_prevlow，均线挂单价)
         ③ 落审计：pick_path / pick_health / ma_line_arm / entry_filter

T 盘中 09:30–15:00（逐 bar 重放，t_monitor 生产间隔 30s）
  每个 bar（5min，必要时 1min）：
    ① 取数：指数 5min（stk_mins / a_share_mins，口径已验一致）+ 该标的分钟 OHLCV
    ② 派生 quote/index 字段：current / pre_close / low / high / open / average(VWAP) /
       vwap_break / dip_prev_low / amplitude / panic_drop / volume_shrink|expand /
       index.m5_dump / index.intraday_dd（**与生产 t_data_sources 同公式**）
    ③ 求值 t_conditions 表达式（253 / 254 / 卖腿 / 止损 / 等量换手）→ 命中即进入执行
    ④ t_gateway 三阶校验：硬闸门(裸空/跌停/STOP_ALL/白名单) → 账本(可卖底仓、买腿≤底仓、
       日亏回转额熔断) → 建议层(单笔≤净值5%、冷却、价差成本比≤20%、频次)
    ⑤ 撮合（BacktestPaperEngine 口径）：整手 100、T+1 冻结、资金校验、跌停禁买/涨停禁卖、
       成交价 = 触发价 ×(1±滑点)，费用见 §3
    ⑥ 更新：持仓/可用资金/等量换手账本(roundtrip_state)/仓位档(position_tier)/T 账本
  15:00 收盘：按收盘价对未成交腿做"当日作废"处理（生产腿按 trade_date 过期）

T 盘后
  15:01  净值快照（现金 + 持仓市值）
  15:05  系统性风险开关（次日闸门）
  15:20  P2 gate 报告    15:25 今日计划
  16:30  指数日线 + mkt_bars 补齐（次日 t_monitor 的前收/支撑口径）
  ⇒ 日终落盘：orders / trades / positions / equity / 拦下原因
```

---

## 2. 每步的"生产代码 = 回测代码"

| 步骤 | 生产模块（**同一份代码**） | 回测需要做的 | 已有先例 |
|---|---|---|---|
| 主题确认 / 主线 gate | `apps/main_line/daily_inputs_chain.py` + heat_v2 / trend_confirm / mainline_gate | 逐日按沙箱 DATA_DIR 重放 | `jobs/stage0_replay_v2.py`（231 个 json） |
| 方向层池 / 子方向 | `derive_sub_universe.py` / `rotation_universe.py` | 加 as-of 打桩（无 `--date`） | — |
| 概念高低位 | `apps/main_line/position_class.py` | 加 as-of；`live_hedge_act()` 直连当日盘口 → **必须打桩回放** | — |
| 选票确认 | `stock_confirm_judge.py` | 加 as-of；AI 裁决部分标注 A | — |
| 布腿 | `jobs/rotation_switch_arm.py`（含 `pick_buy` / `confirm_pick` / `wolf_entry_filters`） | **已有工具**：`jobs/replay_entry_0920.py --date T --cut T-1` | ✅ 已实测（Chiplet 链原样复现实盘腿） |
| 触发判定 | `backend/app/services/t_monitor.py` + `t_expr.py` | 用分钟 bar 驱动同一表达式求值 | 部分：`_bt_batch2/bt254_*.py`（日线级重建） |
| 执行风控 | `backend/app/services/t_gateway.py` | 同代码；无历史盘口的守卫按"不适用"标注 | — |
| 纸交易账本 | `backend/app/core/trading/backtest_paper.py`（T+1/费用/撮合） | 直接复用（注意费用口径见 §7-②） | ✅ 已存在 |
| 等量换手 / T 止盈止损 | `roundtrip_sell.py` / `roundtrip_priority.py` / `wolf_t_rules.py` | 同代码 | 部分：`jobs/eval_replay_m5.py` |
| 仓位档 / 总仓位 | `position_tier.py` + `config/p3_position_tiers.json` | 同代码 | — |

---

## 2.1 两个要带的 agent 怎么纳入（用户拍板：wave agent + 交易腿 agent 带上）

两个 agent 的决策都来自 dsh `/chat`（LLM）→ 非确定。回测要可复现，做法是**录制/回放 + as-of 打桩**：

**① 波浪判定 `apps/main_line/wave_agent.py`（T 08:10）** —— 已实现并实测通过：
* 新工具 `jobs/bt_wave_asof.py`：显式传 `--as-of`（= 生产 08:10 看到的"昨日收盘"）；
  **禁用 `_ensure_index_fresh()`**（它会自愈拉当日收盘并回写 CSV = 前视）；
  `MAIN_LINE_STATE_FILE` 必须指向 as-of 那天的 `main_line_state`（缺文件直接报错，不静默用当期）；
* 实测（as-of 20260911）：`features.date=2026-09-11 / close=3888.1`（与指数 09-11 收盘一致，无未来数据）；
  record 1 次外呼 → replay 命中缓存、**0 次外呼、输出完全一致**（可复现 ✔）。

**② 交易腿 agent `jobs/rotation_switch_agent.py`（T 09:50 / 14:10）** —— 待接线：
* 它自己读 `paper_positions / stock_concept_map / stock_pool / risk_flags` 并**通过 `/trades` 直接下单** →
  回测里把数据源换成沙箱（回测自己的持仓/腿状态），把 `MARCUS_API_URL` 指向回测撮合服务（不碰生产库）；
* LLM 走同一套 `jobs/bt_llm_replay.py`；
* ⚠️ **生产这两条 cron 当前是 OFF** → 回测做 `BT_LEG_AGENT=on/off` 两版：既满足"带上"，又能量化
  "这一层 agent 到底增厚还是拖累"，同时保留与当前生产口径可直接对比的 off 版。

**③ 共用层 `jobs/bt_llm_replay.py`**（已实现）：
* `BT_LLM_MODE=record`（默认）：真实外呼 + 把 `(agent, as_of, prompt, prompt_sha1, reply, ts)` 落
  `data/_bt_llm/<agent>/<as_of>.json`；`replay`：只读缓存，**未命中直接报错**（不静默降级）；
* replay 时若 prompt 与录制不一致（= 输入/PIT 口径漂移）→ 默认**报错**（`BT_LLM_STRICT=0` 可降级为警告）；
* 成本：波浪 1 次/日、交易腿 2 次/日 × 约 180 交易日 ≈ **540 次 LLM 调用**（可分批录制）。

---

## 3. 撮合与费用口径（请你确认）

| 项 | 取值 | 出处 |
|---|---|---|
| 往返费用 | **0.1292%**（佣金万0.86 单边 + 印花税 0.05% 卖出 + 过户费 0.001% 双边 + 滑点 0.0003 单边） | `jobs/eval_aligned_package.py:54`（总账 §10） |
| 另一套（旧）| `backtest_paper.py`：买 0.0005 / 卖 0.0015（卖里含**印花税 0.1%**） | **疑似过时**（现行 0.05%）→ 建议参数化，不混用 |
| 整手 | 100 股整数倍 | A 股规则 |
| T+1 | 当日买入批次当日不可卖（`_buy_lots` 实现） | `backtest_paper.py` |
| 涨跌停 | **跌停禁买 / 涨停禁卖**；接近跌停下低吸档降级 L0 | `t_gateway._near_limit_down` |
| 单笔上限 | ≤ 净值 5%（建议层） | `t_gateway.MAX_SINGLE_ORDER_PCT` |
| 单标的日低吸次数 | ≤ 2 | `t_gateway.MAX_DAILY_BUY_LEGS` |
| 日回转额 | ≤ 3× 净值（熔断） | `t_gateway.MAX_DAILY_TURNOVER_RATIO` |
| 买腿 ≤ 可卖底仓 | 1:1 | `MAX_SELL_FLOOR_RATIO` |
| 成交价假设 | 触发价 ×(1+滑点) 买入 / ×(1−滑点) 卖出；同 bar 只成交一次 | 与 `eval_replay_m5` 一致 |

**已知不可消除的近似**：生产是 30s 轮询、分钟内先后不可知 → 分钟 bar 只能判"是否触及/何时触及"；同 bar 内"先到先成交"的竞争（例如同时触及止盈与破线）按**保守顺序**处理并逐笔记账（V3 口径，见 `eval_replay_m5.py`）。

---

## 4. 数据源与 PIT 纪律

| 数据 | 源 | 状态 |
|---|---|---|
| 日线全市场 + 市值 + 换手 | 生产 PG `mkt_bars_daily`（20241101→20260914，5613 标的） | ✅ |
| 个股/ETF 分钟 1min/5min | `stk_mins`（promax；区间调用，1s 限频） | ✅ 实测 |
| 指数 5min | `stk_mins` 指数码 `000001.SH`（与 `index_5min_dh.json` close 逐 bar 零差异；ClickHouse `a_share_mins` 同源但只覆盖近两周） | ✅ |
| 指数实时（校准用） | 腾讯 `fetch_tencent_mkline('sh000001')`（仅 ~6 日） | ✅ 仅校准 |
| 概念日线 + 资金流 | `concept_hist.json`（521 概念×254 日）+ `concept_long_seed.json`（118 概念 2025-01 起）+ `dc_index` / `moneyflow_ind_dc`（可按历史日拉） | ✅ |
| 新闻/事件 | `news.db`（2025-09-30 起，6.4 万条） | ✅ |
| 腿/触发/成交历史 | 仅 2026-07-31 起 | 只用于**校验** |

**PIT 纪律**：每个 T 日的所有步骤只允许读 `date ≤ T-1`（盘前步骤）或 `≤ T 当前 bar`（盘中步骤）的数据；概念成分用当期 `stock_concept_map`（**披露：非严格 PIT**，成分会漂移）；`rotation_crowd_pit_*` 只有 13 个月度点 → 拥挤度按月度近似并标注。

---

## 5. 逐日产出物（便于对账）

```
data/_bt_runs/<T>/
  pre/            # 08:05–08:40 盘前产物（wave_state / rotation_universe_result / stock_confirm_result / position_class_result）
  arm/            # 09:20 布腿：legs.jsonl（253/254/卖腿）、entry_filter.json、pick_path.json、ma_line.json
  intraday/       # 盘中逐 bar 触发与撮合明细：triggers.jsonl、orders.jsonl、trades.jsonl、blocked.jsonl
  eod/            # 15:01 净值、持仓、等量换手账本、仓位档、次日风险开关
  notes.json      # 本日所有「近似/跳过」的显式标注（A 类产物、无盘口数据等）
```

---

## 6. 校验（跑完先自证，再报收益）

1. **腿级对账**：2026-08-15→09-15 生产 `t_conditions`（switch 腿）vs 回测同日腿 → 逐条比对 symbol / kind / 挂单价；
2. **触发级对账**：生产 `t_triggers`（08-25 起 1,751 条）vs 回测同日触发 → 比对 (symbol, 时间, 类型)；
3. **成交级对账**：生产 `paper_trades`（07-31 起 173 条）vs 回测 → 比对 (symbol, 方向, 价, 量, 费用)；
4. **净值级对账**：生产 `paper_daily_snapshot` vs 回测日终净值曲线；
5. 通过标准：腿/触发**重合率 ≥90%**，差异逐条给出原因（数据源差异 / 无盘口守卫 / agent 链 / 近似）。

---

## 7. 待你拍板（跑之前必须先定）

| # | 问题 | 状态 |
|---|---|---|
| ① | Pi agent 链（09:35/10:35/13:35/14:30 五窗口）是否纳入？ | ✅ **已定（用户）**：**不纳入**；黄金坑 DCA、做T账户同样不纳入 |
| ①b | **波浪 agent + 交易腿 agent** | ✅ **已定（用户）**：**必须带上** → 见 §2.1（录制/回放 + as-of 打桩） |
| ② | 费用口径 | ✅ **已定并落地**（commit `04ef591`）：0.1292%/往返，`backtest_paper.py` 参数化（`BT_FEE_PROFILE=legacy` 可回退） |
| ③ | 起点资金 | ✅ **已定**：`stock` 账户 25 万、空仓起步；`t`/`golden_pit` 不纳入 |
| ④ | 卖出侧的"非腿"卖出（趋势止损/防守减仓/周末避险） | 按建议默认**包含**（确定性规则）；若你不要，回一句我剔除 |
| ⑤ | 概念成分用当期快照（非严格 PIT） | 按建议默认**接受**并在结论标注偏差 |
| ⑥ | 无历史盘口的守卫 | 按建议默认**跳过 + 标注** |
| ⑦ | **新增待定**：交易腿 agent 生产当前 OFF → 回测 on/off 两版是否都要出？ | 建议**两版都出**（off = 与当前生产可直接对比；on = 你要的"带上 agent"） |
| ⑧ | **新增待定**：波浪判定用**回测自录**的 LLM 结论（全年一致、可复现），还是优先用生产当天存档的 `wave_state`（只有 09-11 起）？ | 建议**自录**为主 + 用生产存档做校验（LLM 非确定，混用会破坏可复现） |

---

## 8. 实施顺序（你确认后我按这个开工）

1. **PIT 打桩**：给 `derive_sub_universe` / `wave_agent` / `position_class` / `stock_confirm_judge` 加 as-of（沙箱 DATA_DIR + 日期函数钉住），逐个与当期生产文件比对（先对齐 09-01→09-15 这 11 天）；
2. **布腿链全年化**：`replay_entry_0920.py` 跑 2026-01-05→2026-09-15，产出每日腿；
3. **两个 agent 接线**：波浪（`bt_wave_asof.py` 已就绪）+ 交易腿 agent（`/trades` 改走回测撮合）→ 全年录制 LLM（约 540 次）；
4. **分钟数据**：按第 2 步产物拉 `(symbol, date)` 的 5min（复用 `jobs/fetch_m5_replay.py` 区间模式，缓存本地），指数 5min 全年；
5. **盘中撮合**：分钟 bar 驱动 t_monitor 表达式 + t_gateway 护栏 + `BacktestPaperEngine`；
6. **校验**：§6 五项对账，逐条解释差异；
7. **出结果**：净值曲线 / 分主题分腿型收益 / 与"买而不卖"等基线对照（**最后才谈调参**）。

---

## 9. 实施进展与已发现的口径事实（2026-09-15 夜，第 1 轮）

### 9.1 已建成的部件

| 部件 | 作用 | 实测 |
|---|---|---|
| `jobs/bt_daily_cache.py` | 生产库 `mkt_bars_daily` → 本地 SQLite，提供与生产 `_gz` **同形状**的取数（强制 ≤ as_of） | 2,479,571 行 / 20241101→20260914 / 5,613 标的 / 388MB（≈60s）；与 relay 抽样一致 ✔ |
| `jobs/bt_seed_day.py` | 为决策日 T 建 `_bt_full/<T>/` as-of 沙箱（截断 / 归档 / 再生 / 待打桩 + 软链兜底 + 防写入穿透 + 易变字段归一化） | 09-11 沙箱 ok=28；生产文件 mtime 未被改动 ✔ |
| `jobs/bt_run_pinned.py` | 给**没有 `--date`** 的生产脚本钉时钟 + 钉取数（本地日线）+ 钉 DATA_DIR | 4 个 producer 全部 rc=0 **as-of 再生** ✔ |
| `jobs/bt_day_legs.py` | 在沙箱里重放 09:20 布腿器买侧（主题门 → 路径A/B → 板前过滤 → 两道入口门 → 253/254） | 09-11 跑通（56s）✔ |
| `jobs/bt_llm_replay.py` + `bt_wave_asof.py` | 波浪/交易腿 agent 的 LLM 录制-回放 | **波浪 as-of 重放与生产存档逐字段一致**（d4/4-3/side）✔ |

### 9.2 已验证的"完全一致"（强证据）

1. **波浪判定**：as-of 20260910 重放 = `d4/4-3/side`，与生产 `daily_artifacts`(trade_date=20260911) 一致 ✔
2. **方向层 D1 选主**：`wolf_mainline_select.py --date 20260910` 再生结果 = `mainline=农业,
   pool=[农业, 消费/内需, 金融, 稳增长/基建, 电力/公用, …]`，与生产同日 `daily_artifacts.mainline_select` **逐字段一致** ✔

### 9.3 本轮踩到并修掉的坑（都会让结论失真，值得记）

1. **钉时钟不能替换 `datetime.date/datetime` 本身** —— pandas/numpy 的 C 扩展会因 PyObject 尺寸变化**直接 SIGSEGV**（rc=-11）；
   正确做法：**先 import pandas/numpy 预热**，再打补丁，且补丁类的 `today()/now()` **返回真实 date 实例**。
2. **relay 替身只能替换 `relay_items`，不要动 `get_relay`** —— 原版 `relay_items` 内部调 `get_relay()`，
   两者都换 → shim→原函数→shim 的**无限递归**（RecursionError，表现为"所有 relay 调用 0.0s 失败"）。
3. **regenerate 类脚本会顺手改写其它状态文件** —— `wolf_mainline_select.py` 会把 `main_line_state.json` 重写；
   若不快照/还原，后续 `stock_confirm_judge` 读到的是"再生后的 main_line_state"，主题就会选偏
   （实测：确认域从生产口径的 AI/半导体族变成 农业/金融族）。→ 现已在 regen 后与 producers 前后**双次还原**。
4. **`main_line_state.json` 的取源要用"≤ T 日早晨"**，不是"≤ cut"：T 日 08:20 的进程看到的是
   **T 日 08:00 写的**那份（实测 09-11：`updated_at=2026-09-11 08:00:46`，`main_line=稳增长/基建`）。
5. **`theme_nets()` 返回 `List[float]`（不是 dict）**：资金门要 as-of 就**传 `as_of=cut`**，
   按 dict 过滤返回值会让序列长度变 0 → 资金门 fail-closed → 全部主题"停止买入"（并**真的发了 QQ 告警**）。
   回测里必须把 `notify_once/_send_qq` 打成空操作，否则跑一年会刷屏。
6. **归档里的"当日覆盖型"文件可能是盘后版本**：`_archive/<T>/stock_confirm_result.json` 是 **19:50 快照**，
   而它当日被 18:55 的方向层链重写过 → 拿它当"T 日 08:20 的口径"比对会误判（本轮先踩，后改用 arm 日志当基准）。
7. **psycopg2 里 `LIKE '买%'` 的 `%` 会被当占位符**（IndexError）→ 改 `= '买入'`。

### 9.4 ⚠️ 结构性发现：**腿有两个来源**，只重放 09:20 会漏一半

生产 09-11 的 18 条 switch 条件（9 只票）是 **08:18** 创建的吗？不是——是 **08:18** 由
`jobs/tranche_ladder_report.py`（`SWITCH_AUTO_EXEC=1`）→ `switch_builder.build_plan()` 布的；
而 **09:20** 的 `rotation_switch_arm` 当天 `buy_chains=[]`、`ARMED []`（一条没布）。
反过来 09-15 是 09:20 布的（id 696–701）。
⇒ **全拟真的腿层 = `switch_builder`（08:18 试仓档/清单）∪ `rotation_switch_arm`（09:20）**，
两者都写 `publisher='switch'` 的条件。只重放后者会让腿数偏少（实测 09-11：2 vs 9）。
下一步：把 `switch_builder.build_plan()` 也纳入沙箱重放，并以 `logs/rotation_switch_arm/*.json` +
`_archive/<d>/db_t_conditions.csv` 做逐日对账基准。

### 9.5 第 2 轮（2026-09-15 夜）：把「代码版本」也做成 as-of —— 并且拿到了 **9/9 的腿级完全一致**

**新增的第 0 层：按日的代码版本树**（`jobs/bt_code_revs.py`）
* 生产代码 2026 年一路在改（09-11、09-13、09-14、09-15 都有影响买入链的提交：如 09-13
  「gate 整块退场 / 主题来源改方向层池」、09-15「两道建仓门 + 均线挂单 + v3」）。**用今天的代码回放 1 月 = 把后来的机制提前装上**。
* 做法：每个交易日解析"当天在跑的版本"= 该日之前最后一次触及链路的提交 → `git archive` 出**代码版本树**
  （10 个不同版本 / 11 天，89MB，落在 `data/_bt_code/rev_<rev>/`，容器内 `/app/data/_bt_code/...`）；
  回放时把该树的 `apps/main_line, jobs, backend, core, config` **置顶到 `sys.path`**。
* 三个必须做对的地方（都踩过）：
  1. **入口脚本也要用版本树里的那份**：`runpy`/`subprocess` 跑的是"这个文件"，只有 import 才走 sys.path
     → 只改 sys.path 会让"入口脚本是今天的、被 import 的模块是旧的"。现在按 `script_path(code_dir, rel)` 解析；
  2. **装 shim 会打乱 sys.path 顺序**：`bt_run_pinned` 在 import 时把 `/app*` 插到最前
     → 必须**在它之后再插一次版本树**（否则 `mainline_confirm_state` 用的是现行版：
     实测 `gate_top_themes` 不存在 → 回退 `main_line_state.fusion` → 主题从「稳增长/基建」变成「农业/消费/金融」）；
  3. **老版本的取数协议不同**：09-13 之前的脚本走 **gzcloud HTTP**（服务已失效），
     relay 替身接不到 → 新增 `GzcloudShim`：识别 `POST {api_name, params, fields}` → 本地 SQLite 应答
     （trade_cal / daily / daily_basic 全覆盖，其余回落 relay）。

**时钟打桩的补全**：`time.strftime/localtime/gmtime/ctime/asctime` 也必须钉 —— `time.strftime('%Y%m%d')`
用的是 `localtime()`（C 层读系统钟，**不走** `time.time()`）→ 只钉 `time.time` 会让 `upto=今天`，
于是 `glob('mainline_gate_*.json')` 挑到**最新那天的 gate**（实测：09-11 的回放读到了 09-15 的 gate，
第三个主题从 农业 变 资源/周期，确认域整块跑偏）。

**2026-09-11 的对账结果（生产 `_archive/20260911/` + `t_conditions` 为基准）**

| 层 | 回放结果 | 生产实际 | 判定 |
|---|---|---|---|
| 波浪判定 `wave_state` | `d4 / 4-3 / side`（as-of 09-10） | 同 | ✅ **完全一致** |
| 方向层 D1 `mainline_select` | `mainline=农业, pool=[农业,消费/内需,金融,稳增长/基建,电力/公用]`（09-10） | 同 | ✅ **逐字段一致** |
| 确认域 `stock_confirm_result`（09-11 08:20 口径） | 47 概念 / 417 成员 / stage 分布 {下跌中173, 缩量止跌151, 结构到位67, 确认15, 突破候选11} | **完全相同** | ✅ **47/47 概念、417/417 成员一致** |
| **08:18 布腿路径**（`tranche_ladder_report` → `switch_builder.build_plan()`） | `buy_new` = 000065 / 600977 / 600039 / 600284 / 000401 / 600449 / 603737 / 600586 / 002613 | 生产 09-11 实际布腿 **同样 9 只** | ✅ **9/9 完全一致** |
| 09:20 布腿路径（`rotation_switch_arm`） | 出 2 条腿（Kimi概念/免税概念） | 当天 `buy_chains=[]`、**一条没布** | ❌ 未对齐（见下） |

**关键时序发现（只有逐日对账才会暴露）**：`switch_builder` 在 **08:18** 跑，而 `stock_confirm_judge`
在 **08:20** 才刷新 → 所以 08:18 用的是**上一个交易日**的确认域（实测：用 09-11 口径的确认域 → 13 只候选、
与生产 9 只只重合 6 只；换成 **09-09 口径**（= 09-10 08:20 写的那份）→ **正好 9 只，完全命中**）。
⇒ 回测里"同一个文件在不同时点看到的是不同版本"，必须按**任务时序**而不是按"当天日期"来取。

**仍未对齐的一处（下一轮目标）**：09:20 路径的 `rotation_universe_result.json` ——
生产 09-11 早上 `room_bottom/holdT_top` 为空（所以 `buy_chains=[]`），而我们的 as-of 再生给出了
`Kimi概念 / 免税概念` 两条链。疑似它的输入里还有未打桩的（`rotation_universe_classified.json` /
`rotation_proxy_state.json` / `concept_long.json` 等"当日覆盖型"）。对齐它 09:20 那条路径才能收敛。

### 9.6 第 3 轮：09-11 **两条路径全部对齐**（09:20 的根因是"资格集合取法随版本不同"）

上一轮 09:20 路径未对齐（生产 `buy_chains=[]`，我们给出 2 条链）。本轮查到根因并修掉：

* **根因**：`room_bottom`/`holdT_top` 两边其实**一致**（生产 09-11 的 `rotation_universe_result` 实测
  `crowded_top=[AI应用] / holdT_top=[] / room_bottom=[Kimi概念, 免税概念, 短剧互动游戏]`，与我们的再生一致）。
  真正不同的是**放行资格集合的取法**：09-13 之前用 `gate_confirmed_today()`（gate 的 `confirmed_candidate`
  = 当天只有 `稳增长/基建`），09-13 起才改成 `mainline_today()`（方向层池）。
  我的回放脚本写死了新版函数 → 拿到 13 个主题 → 免税/Kimi/短剧 三条链全部放行 → 多出 2 条腿。
* **修法**：脚本改成**随版本自适应**——模块里有 `mainline_today` 就用它，否则用 `gate_confirmed_today`
  （与各版本 `main()` 自己的取法一致）。

**2026-09-11 最终对账（两条路径）**

| 路径 | 回放 | 生产 | 判定 |
|---|---|---|---|
| 08:18 `switch_builder`（含 09-09 口径确认域） | 9 只：000065/600977/600039/600284/000401/600449/603737/600586/002613 | 同 9 只 | ✅ **9/9** |
| 09:20 `rotation_switch_arm` | 资格集合=`['稳增长/基建']` → 3 条链全部 `SKIP_MAINLINE_LOWBUY` → **0 条腿** | `buy_chains=[]`、`ARMED []` | ✅ **0/0** |
| 波浪 / D1 选主 / 确认域 | 见 §9.5 | — | ✅ 完全一致 |

⇒ 09-11 单日**全链路一致**（腿级 100%）。同日还新增多日流水线 `jobs/bt_days.py`：
按日 `seed → (上一交易日确认域) → 08:18 路径 → 09:20 路径 → 汇总 legs_all.jsonl/legs_by_day.json`。

### 9.7 第 3 轮（续）：多日流水线 + 腿级对账（09-08 → 09-14）

新增两个工具：
* `jobs/bt_days.py`：多日流水线（按日 `seed → （上一交易日确认域）→ 08:18 路径 → 还原当天确认域 → 09:20 路径 → 汇总`）；
* `jobs/bt_compare_legs.py`：**腿级对账**（回测 `legs_by_day.json` vs 生产 `t_conditions`：
  `publisher='switch'` ∧ `direction='buy'` ∧ kind ∈ {custom_m5dump, custom_prevlow}，并只用交易日历）。

**对账结果（第一版，5 天）**

| 日期 | 生产实盘腿 | 回测腿 | 重合 |
|---|---|---|---|
| 20260908 | 13 条 | 0 | 0/13 |
| 20260909 | 25 条 | 5 | 0/25 |
| 20260910 | 11 条 | 3 | 3/11 |
| **20260911** | **9 条** | **9 条** | **9/9 ✅ 召回/精确均 100%** |
| 20260914 | 13 条 | 0 | 0/13 |
| 合计 | 71 | 17 | 12 → 召回 17% / 精确 71% |

**已知的两个"仪器误差"（先修掉才谈对齐）**：
1. 对账 SQL 必须限 `publisher='switch'` 且只用交易日 —— 不加会把 agent/做T 的腿、以及 09-12/09-13 周末都算成"生产布了腿"；
2. 流水线里 08:18 用"上一交易日确认域"跑完后**必须还原当天口径**再跑 09:20（已加 `stock_confirm_result_cut.json` 备份/还原）。

**09-11 之所以 100%，是因为把三个中间量都验过**：资格集合（`gate_confirmed_today` = 稳增长/基建）、
确认域（47/47 概念、417/417 成员、stage 分布一致）、以及 08:18 的候选命中（9/9）。
其余日子的差距要在同一套中间量上逐日查（下一步）：
* 09-08 / 09-09（生产 13/25 条农业·消费族，回测 0/5 条）：先查该日**版本树里 `switch_builder.fusion_top3` 用 gate 还是 fusion**（09-13 前是 `gate_top_themes`，
  但更早的版本可能读 `main_line_state.fusion`），再查该日 `mainline_gate_<d>.json` 是否被我们播种成了 **DB 里那份 rebuilt**（实测 rebuilt 的 rows 与真实文件不同）；
* 09-10：部分重合（3/11）——确认域/主题两个中间量逐条比；
* 09-14：回测 0 条 —— 该日起主题来源已切到方向层池（`mainline_top_themes`），要确认沙箱的 `wolf_mainline_select.json`（as-of 09-11 再生）与生产当天读到的一致。

### 9.8 第 4 轮：09-08/09-09 的**有界缺口** + 盘中层开工（分钟数据 + 复用生产回测引擎）

**① 09-08 / 09-09 不可精确复现（有界缺口，不是 bug）**
这两天生产 `switch_builder` 的主题来源是 `main_line_state.json` 的 **`fusion`** 分数（那两天的版本里
`mainline_confirm_state` 既没有 `gate_top_themes` 也没有 `mainline_top_themes`）。而 `main_line_state.json`
是**每日覆盖型**文件，09-07/09-08 的版本已丢：归档 `_archive/<d>/` 从 08-11 才有、且只有"带日期产物"，
DB `daily_artifacts.main_line_state` 只从 08-11 起，旧版 dated 文件（`main_line_state_2026-09-03.json` 等）
在 09-03 之后就没有了。⇒ 结论：**要精确复现，必须从 08-11（G2 每日存档上线）起；09-10 起腿级已可 100%**
（实测 09-10 3/3、09-11 9/9、09-14 0/0 与生产一致）。

**② 生产自己的 08:18 计划日志 = 最好的地面真值**
`logs/tranche_ladder_report/*.json` 每天打印 `buy_new(新方向低吸): …`：
09-08 13 只 / 09-09 10 只 / 09-10 3 只 / 09-11 9 只 / 09-14 **无** / 09-15 **无** ——
与我们的重放逐日对比：**09-10、09-11、09-14 完全一致**，09-08/09-09 因上述 `fusion` 缺失而不一致。

**③ 盘中层：分钟数据 + 复用生产回测引擎**
* `jobs/bt_fetch_mins.py`：按 (symbol, day) 拉 `stk_mins` 5min（含指数码）→ `data/_bt_full/mins/`；
  实测 9 只腿标的 × 3 天 + 指数 = 27 个文件 / 1315 根 bar（**有 3 个组合首次失败，已加重试重拉**）。
* `jobs/bt_pack_mins.py`：把分钟缓存打成生产回测引擎认识的布局
  （`m5/<sym>.json`、`index_m5/sh.json`、`index_daily/<ts>.json`、`stock_daily/<sym>.json`）；
  两个格式坑：引擎 `build_snapshot_at()` 要求 `time` 是 **`YYYY-MM-DD HH:MM:SS`**（不能用 12 位紧凑串）；
  `mkt_bars_daily` **不含指数** → 指数日线要从 `data/指数数据/index_daily/000001.SH.csv` 兜底（实测 3090 行）。
* `jobs/bt_intraday.py`：用 `TBacktestEngine`（生产自带的 m5 回放引擎：逐 bar 快照 → 条件求值 → 护栏 → 撮合）
  跑单标的单日，并与生产 `t_triggers` 对账。**生产 09-11 的地面真值**：SH600039 只有 `custom_prevlow`
  触发过（首次 11:00:24，21 次重试），`custom_m5dump` 未触发；SH600977 触发 13 次。

### 9.9 盘中层：**生产回测引擎不能直接拿来跑 253/254 腿**（第 4 轮结论）

`backend/app/services/t_backtest.py::TBacktestEngine` 是现成的 m5 逐 bar 回放引擎（快照重建 → 表达式求值 →
护栏 → 撮合），我原打算直接复用。实测结论（值得记住，避免下一轮重复踩）：

* **能用的部分**：`build_snapshot_at()` 的字段（`quote.*` 全套派生、`vol_ratio`、`minute.*`、`index.*`、`tech.*`）
  与 `t_expr.evaluate_expression()`（**生产同一个表达式求值器**、支持 `a.b.c` 点路径）——
  所以快照可以直接复用，只需注入两个 253/254 专用字段：
  `quote.dip_prev_low`（当日最低 ≤ 前一交易日最低×(1+tol)，同 `t_monitor._stock_dip_prev_low`）与
  `index.m5_dump`（指数 5min 单根跌幅，同 `t_monitor._index_m5_dump`）—— 已在 `jobs/bt_intraday.py` 里以
  "包一层 `build_snapshot_at`"的方式实现（不改生产文件）。
* **不能用的部分**：主循环是**为做T账户写死的** —— `trigger_kind` 只认 `high_sell/high_sell_then_buy_back/
  low_buy/panic_vibrate`，买腿还带"无底仓不评估"预拦截；换成我们的 `custom_m5dump/custom_prevlow` 时
  **条件会被求值但不产出任何事件**（实测 `status=completed, events=0`，而生产当天 SH600039 触发过
  `custom_prevlow` @11:00:24）。
* ⇒ 下一轮做法（已定）：**自己写 tick 循环**（5min bar + 指数 bar 驱动），复用上面的快照与求值器，
  触发后用 `BacktestPaperEngine` 撮合（T+1 / 100 股整手 / 跌停禁买·涨停禁卖 / 0.1292% 往返），
  用**生产 `t_triggers`（join `t_conditions` 得到 253/254 类型）** 做地面真值对账：
  09-11 SH600039 的 `custom_prevlow` 首次触发 11:00:24（重试 21 次）、SH600977 触发 13 次、其余 7 条腿未触发。

### 9.10 第 5 轮：**盘中触发已对齐**（09-11 九条腿，首次触发差 ≤1 根 5min bar）

自写 tick 循环 `jobs/bt_tape.py`（5min bar 驱动，复用生产 `t_expr.evaluate_expression` 求值器）：

| 标的 | 回测（首次触发 / 次数） | 生产 `t_triggers` | 判定 |
|---|---|---|---|
| SH600039 | 11:15 / 28 | 11:00:24 / 21 | ✅ 同类、首触发差 1 根 bar |
| SH600977 | 13:10 / 21 | 13:08:04 / 13 | ✅ 同类、首触发差 **1 根 bar（2 分钟）** |
| 其余 7 条腿 | 0 | 0 | ✅ 一致（当日确实没触发） |

**做对的三件事（口径都要跟生产一致，否则触发时刻会整体偏）**：
1. **`vol_ratio` 是"换手节奏比"不是"分钟量比"**：生产 `calc_volume_ratio_at()` =
   `[当日累计换手% × (240/已开盘分钟)] / 基准换手%（近5个已完成交易日 turnover_rate 均值，存在条件的
   benchmark_turnover_profile 里）`。回测里当日累计换手读不到 → 用**等价变形**（基准换手会约掉）：
   `vr ≈ [累计量 / 前5日平均日量] × (240/已开盘分钟)`，**不需要未来数据**。
   ⚠️ 单位坑：tushare 日线 `vol` 是**手**、分钟 bar 的 `vol` 是**股** → 日量必须 ×100（否则差 100 倍，
   实测会从"偏早触发"变成"完全不触发"）。
2. **同槽多行必须先聚合**：部分标的的 `stk_mins`（或 `a_share_mins` 兜底）返回的是**分钟级多行**，
   同一 5min 槽的 `time` 重复 3~4 次（实测 SZ000401 每天 196 根）→ 不聚合会让"累计量/量比"重复计数。
3. **`quote.average` 要自己算**：生产是实时字段（累计成交额/累计量），回测快照里没有 → 用当日累计 amount/vol。

**已知残余偏差（都 ≤1 根 bar，方向一致）**：生产是 30s 轮询、且用腾讯实时序列，回测是 5min bar 收盘评估，
所以"阈值在 bar 内穿越"的情形会晚 0~5 分钟；次数上回测每根 bar 判一次（28 次）而生产是 5-6 分钟一次（21 次），
属于**评估频率**差异而非判据差异。

### 9.11 第 6 轮：**生产买入链的时间边界**——"全年忠实回测"在 1–8 月不成立（三源证据）

起因：对 09-10 逐条查 `t_conditions.created_at` 时发现当天有 **4 次布腿**（08:18/09:20/10:14/14:11），
09-14 有 **2 次**（07:53/09:21），而我只建模了 08:18 与 09:20 两条 —— 必须判定"哪些布腿是**代码路径**、
哪些是**人工/agent 顺手跑的**"，否则对账分母是错的。

三源证据（互相独立，结论一致）：

| 源 | 覆盖 | 能回答什么 |
|---|---|---|
| ① `logs/scheduler_<date>.jsonl`（106 天，2026-06-03→09-16） | 全窗口 | 每个**任务**几点跑、输出里布了哪些腿 |
| ② 75 份**逐日代码树**的 `config/tasks.yaml`（`data/_bt_task_timeline.json`） | 全窗口 | 某个任务**何时被写进配置、是否 `enabled`、cron 几点** |
| ③ `t_conditions`（DB，保留期只到 2026-08-15 起） | 近 1 个月 | 实际被布腿的标的与**时刻**（含非任务通道） |

**结论 A：规则买入链 2026-09-03 / 09-07 才上线。**

| 任务 | 配置首次出现 | `enabled` 首日 | 首次实际运行 | cron |
|---|---|---|---|---|
| `rotation_switch_arm`（09:20 布腿） | 2026-09-03 | 2026-09-03 | 2026-09-04 09:20 | `20 9 * * mon-fri` |
| `stock_confirm_refresh`（08:20 个股确认） | 2026-09-07 | 2026-09-07 | 2026-09-07 08:20 | `20 8 * * mon-fri` |
| `tranche_ladder_report`（08:18 建仓报告） | 2026-09-07 | 2026-09-07 | 2026-09-08 08:18 | `18 8 * * mon-fri` |
| `rotation_universe_refresh`（08:05） | 2026-09-07 | 2026-09-07 | 2026-09-07 08:05 | `5 8 * * mon-fri` |
| `sector_g3_judge`（08:15） | 2026-09-07 | 2026-09-07 | 2026-09-07 08:15 | `15 8 * * mon-fri` |
| `mainline_gate_daily`（18:45） | 2026-09-11 | 2026-09-11 | 2026-09-09 18:45 | `45 18 * * mon-fri` |
| `daily_decision_am` / `daily_inputs_chain` / `wolf_mainline_select` | 2026-09-14 | 2026-09-14 | 2026-09-14 | `25 8` / `45 18` / `55 18` |

2026-06-03 → 09-02 的启用任务只有：`news_collector`、`market_scan`、`pre_market_scan`、`auto_trade_*`（5 窗）、
`daily_review`、`weekly_reflect`、`stock_pool_refresh`、`fund_flow_cache`、`daily_snapshot`、`morning_diagnosis`、
`golden_pit_*`。**没有任何"规则选股→布腿"任务** —— 那段时间唯一在买的是 **Pi 自动交易 agent**（用户已排除）
与 **黄金坑 DCA**（已排除）。同时 `rotation_switch_dryrun` / `rotation_switch_agent_morning|afternoon` /
`mainline_open_buy` / `taxonomy_classify` 虽已登记但 **`enabled: false`**（agent 已并入 auto_trade），不构成路径。

→ **"忠实回测"的有效窗口 = 2026-09-03 → 2026-09-15（9 个交易日）**；1–8 月只能跑**反事实**（把 9 月才
存在的链套到旧数据上）。实测反事实也没有意义：全窗口流水线在 2026-06-03→06-17 逐日跑出 **0 条腿**
（`_days_year.log`），而这与真值一致（那几天生产本来就没有腿）—— 说明这段重放**没有信息量**，
60 余天的排队重放可以停掉（省数十小时）。

**结论 B：布腿有**三条**通道，第三条不可复现（对账必须分层）。**

| 通道 | 触发方式 | 痕迹 | 实测例子 |
|---|---|---|---|
| ① 调度任务 | cron | 任务日志 + scheduler 行 | 每天 08:18 tranche / 09:20 arm |
| ② 手工"立即执行"任务 | GUI/agent 点任务 | 任务日志 + scheduler 行（时间任意） | 09-09 08:15 arm、09-10 14:11 arm |
| ③ **手工/agent 直接跑脚本** | `docker exec python jobs/rotation_switch_arm.py` | **无任何日志** | 09-09 07:26(7)/08:56(2)/13:25(2)、09-10 10:14(3)、09-14 07:53(9) |

通道 ③ 的证据：`find /app/logs -newermt <时刻前> ! -newermt <时刻后>` 在这些时刻**没有任何文件**；
scheduler jsonl 无对应行；宿主与容器都**没有** crontab / systemd timer 能产生这些时刻
（宿主 crontab 只有腾讯云 stargate；`systemctl list-timers` 无 marcus 相关）。且这些腿的 `expression`
与调度通道**完全同构**（`custom_m5dump` + `custom_prevlow`）→ 同一段代码、只是被人手工触发。

**逐日量化（`jobs/bt_legs_truth.py --with-db`）**——"调度通道真值" vs "DB 实际"：

| 交易日 | 调度通道（可复现） | 构成 | DB 实际 | 仅 DB（通道②③/周末痕迹） |
|---|---|---|---|---|
| 09-04 | 0 | arm 只布了卖腿 `sell_vwap_break` | 0 | 0 |
| 09-07 | 0 | arm 09:20 决策为空 | 10 | 10（17:47/18:40 手工） |
| 09-08 | 13 | tranche 08:19→13 | 13 | 0 |
| 09-09 | 14 | arm 08:15(2) ∪ tranche 08:18(10) ∪ arm 09:20(2) | 25 | 11（07:26/08:56/13:25） |
| 09-10 | 8 | tranche(3) ∪ arm 09:20(3) ∪ arm 14:11(3) | 11 | 3（10:14） |
| 09-11 | 9 | tranche(9) ∪ arm 09:20(空) | 9 | 0 |
| 09-14 | 4 | tranche(空) ∪ arm 09:20(4) | 13 | 9（07:53 = **把 09-11 的计划重布一遍**） |
| 09-15 | 3 | tranche(空) ∪ arm 09:20(3) | 3 | 0 |
| 09-12/09-13（周末） | — | 无任务 | 9 + 9 | **开发期测试痕迹**（非生产行为） |
| 合计 | **51** | 6 个交易日 | 102 | 33（工作日手工）+ 18（周末） |

→ 因此**对账分母分两层**：**A 层 = 调度通道**（可复现，`≥90%` 目标按 A 层判）；**B 层 = 全部生产腿**
（含人工通道，只作信息给出，并注明 09-12/09-13 是开发痕迹）。DB 本身也不完整（`t_conditions` 保留期
2026-08-15 起，09-04 的行已不在）→ **真值必须以 scheduler 日志 + 代码树为准，DB 只做交叉校验**。

**本轮修掉的一个真 bug（会让分母虚增）**：`ARMED` 里**买卖腿混在一起**（`buy_253`/`buy_254` vs
`sell_vwap_break`）—— 09-04 的 4 条 ARMED **全是卖腿**，第一版抽取器把它们当成买腿 → 分母虚增、
且 09-04 被误列为"有腿日"。已修（只认 `type` 以 `buy` 开头），真值随之从 55 腿/7 天 → **51 腿/6 天**。
