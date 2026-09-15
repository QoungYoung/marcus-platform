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
