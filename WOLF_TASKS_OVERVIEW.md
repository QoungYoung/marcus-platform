# 狼大交易策略复制 — 任务总览（大周期 / 小周期 / 已完成）

> 生成：2026-09-01；更新：2026-09-03（第二次切换上下文快照；commit 见 git log，本快照含 v3 92.9% 等全部当日进展）
> 目标：逆向复刻狼大(-阿狼-)完整 A 股交易策略。
> 已完成：主线判定、浪型v6、高低位v2、确定性门槛、做T体系、主线内轮动/细分宇宙、P2 风控主体、P2 宏观 v1/v2（含历史关键时点回填）、P2 Gate 统一接入（Step1/2）、业绩/产业/政策日历、E01-E04 分钟补拉与买点一致率 v3（92.9%）。

## 1. 已完成部分

### 1.1 主线判定（main_line_judge）✅
- fusion_mainline 生产权重 (catalyst=0, fund=0.3, rel=0.2, conc=0.5)；研报 catalyst 已被语料审计归零。
- 22 标注日期重放 81%；OOS 5/7=71%（docs/mainline-oos-validation-report.md）；主线历史按日期取。

### 1.2 浪型级别判定（wave_agent）✅ 冻结 v6
- 两级 schema（level/sub_level/operation）；gate：defense/exit 硬拦不建仓；wave_context 接 agent 输出，缺文件回退 rule-based。
- 回测方向 75%/级别族 83%；调度周一 8:10；data/wave_state.json + wave_config.md。

### 1.3 高低位分类（position_class）✅ v2 79%（真实水平）
### 1.4 确定性门槛（confirm_chain）✅ P1（指数确认 60d hit 1.00）
### 1.5 做T体系 ✅
- 正T语义修正；5min 急杀 253 / 触前低缩量 254 / 分时T出 250 / 黄线破位 252；底仓保护 100 股；TMonitor 30s 5 腿持续运行；ETF(Wolf T monitor SH588170 base-floor 66,900) 已入 stock 账户观测。

### 1.6 买点对齐 Wolf ✅ v2→v3（2026-09-03）
- 误加逻辑审计结论：MA5>MA20/KDJ/RSI/CCI/午后禁开 = 软约束（LEGACY_TECH_GATES=1 可回退）。
- 评测链唯一事件判定：judge_wolf_event_alignment.py（v2/v3 双口径输出）。
- **v2（历史口径）**：8 事件（E05-E13）39 行；同日 17.9%；±5 日 35/39=89.7%。
- **v3（官方，E01-E04 并入；E15 按用户决定不并入）**：12 事件 56 行；同日 18/56=32.1%；±5 日 **52/56=92.9%**；12/12 事件 aligned（avg 93.1%）。E01/E02=intent_open（开盘建仓/probe≤3% 假设）、E03/E04=stepwise B（253 大盘急杀时点暂用 510300 宽基代理）；caveats 详见 v3 文件。
- 底层回放：backtest_wolf_253_stepwise.py（B 语义）+ backtest_wolf_extra_events.py（E01-E04）；数据 stepwise_253_backtest_B(_extra).json。

### 1.7 P2 主线内轮动/产业链形态 ✅ + 拥挤个股级 PIT v2 + 13 时点真 PIT
- rotation_gate v2 + trade_graph 轮动门控；拥挤过滤 整概念→公募核心 17 只（PIT，ann_date 防前视）。
- 13 个双周时点真 PIT 重跑（build_fund_pit + backtest_rotation_quadrant_pit.py）：总相邻转换率 23.4%；51/180 格子修正季度近似口径（一致性 71.7%）。

### 1.8 P2 风控主体 ✅
- risk_gate + risk_flags（forecast/express/ST）+ check_entry_filters 硬拦 + systemic_risk 15:05。
- 剩余：公告类 source=ai（anns 403）。

### 1.9 P2 宏观/机构行为 ✅ v1/v2 + 历史关键时点回填（2026-09-03）
- macro_state 采集器（15:06）：akshare CN/US 2/5/10/30Y + 新浪 DXY（仅实时）+ tushare 两融/GJD(510300/510050 份额)/北向/龙虎榜外资。
- Wolf 四类开关 v2（margin_burst/lhb_foreign_sell/gjd_withdraw|support/yield_spike 等）→ trade_graph 宏观上下文。
- A/B M01-M10 **10/10**：即时源与回填快照双口径一致。
- **历史回填**：backfill_macro_state_history.py 回填 19 个关键时点（E05-E15∪M01-M10）→ data/macro_state_history.json（E13 07-08 = margin_burst+gjd_withdraw，GJD 护盘 07-23 才在份额显现；E13 缺动作非宏观漏判）。

### 1.10 P2 Gate 统一接入交易 ✅ Step1+Step2（观察中）
- backend/app/services/p2_entry_gate.py：wave defense/exit、systemic level≥2、macro margin_burst → 硬拦；lhb_foreign_sell 方向感知（config/p2_macro_direction_map.json）；gjd_withdraw/yield_spike → 软降。
- p2_gate_log.jsonl + p2_gate_daily_report（15:20）。

### 1.11 日历类 ✅（2026-09-03）
- 业绩披露日历 v2（disclosure_date pre/actual；12 只 watch；每日 08:40）；H1/H2 产业节奏 + 政策会议日历 config（四中全会 10-23/两会 3-04/CEWC 12 月等）。

### 1.12 分钟数据/回放底座 ✅（2026-09-03）
- brze（tushare 兼容源）stk_mins：个股+ETF 5min 48 根/日、1min 241 根/日，可回溯 ≥2024-01。
- backfill_minute_windows.py 补拉 E01-E04（2025-08~10）/E15（2026-08）窗口 → stock_5m_bt 28→37 文件（新增 603296 华勤/603986/688008/300475/001309/ETF 512480/159995/588200/510300 大盘代理）；断点续跑+节假日 .skip。
- 限制：brze idx_mins（上证指数分钟）tenant key 过期未续；akshare EM 指数 5min 断连 → E01-E04 的 253 大盘时点暂用 510300 代理。
- data/strategy_history/（2026-05-25 起逐日 Pi 策略快照：trades/intraday_scans/watchlist/pi_confirmation）可回查 E12/E13 当日决策。

### 1.13 审计/对齐工具 ✅
- alignment_audit.json（39 行三方审计）、audit_trade_three_way.py、wolf_reason_events.json（15 事件理由→信号→数据需求）、docs/data-gap-inventory.md。

## 2. 生产架构（2026-09-03 更新）

- stock 账户：药明康德底仓 100 股 + SH588170 ETF base-floor 66,900；做T 5 条持续腿（249/250/252/253/254，TMonitor 30s）；auto_trade 5 窗口任务 enabled。
- 调度（config/tasks.yaml，25 个任务条目）：周一 8:00 main_line / 8:10 wave / 8:20 position(链)；周日 8:00 stock_pool / 8:05 宇宙分类；季度 fund_crowding；每日 08:40 earnings_calendar_refresh、15:05 systemic、15:06 macro_state_collector、15:20 p2_gate_daily_report。
- 候选拦截链（新开仓统一过 check_entry_filters）：
  risk_flags(业绩/ST) → crowding_blacklist(个股级) → P2 Gate(wave/systemic/macro) → 技术/资金/形态（Wolf 对齐软约束）→ calc_position（P3 三仓档位 dry-run 记录）。
- 回退开关：LEGACY_TECH_GATES=1；P2_GATE_MODE=1（0=dry-run）；P3_TIER_MODE=0（只记录不拦）。
- 数据/脚本清单见 §5 索引。

## 3. 任务清单（剩余）

### P2（或 P1 挂起项）
1. P2 宏观收尾（观察型）：DXY 历史源自建快照；崩盘清单缺失源（期指空单/30Y 放量/券商破位）；板块级外资/龙虎榜覆盖（红利核心大票）；policy_floor 事件日历（可先 config 人工，E13 政策底/电芯涨价类）；2 周 p2_gate_log 观察后校准阈值。
2. P2 主线剩余：科技子类粒度（AI硬 vs 半导体）+ 机器人/互金主题覆盖（依赖 P1 标注，用户曾暂缓）。
3. P2 风控剩余：公告类立案/重组/监管 source=ai（anns 403，等 news/涨价/发布会类事件源）。
4. P2 Gate Step3：观察 2 周 → 校准 → 定稿（P2_GATE_MODE 最终值）。

### P3 / 买点生产化
5. P3 intent 闭环生产：monitors/Pi 真用 refill/add/probe + probe 确认链（行为变更，需用户确认）。
6. 253 B 语义 + 分步小仓接 TMonitor（用户曾"先不对接"）。
7. ETF 执行通道（E08/E14；账本/券商支持）。
8. E01/E02 intent_open 仅是回测假设 → 需"开盘主线候选建仓/probe"生产通道 + buy_point_log/strategy_history 前向验证。
9. E15（08-12 买回半导体，代理 w5 3/7=0.43）暂不并入 v3；E14 无完整代理篮；E07（月计划）不计。

### 数据层缺口（影响一致率上限/归因）
10. brze idx_mins 续 key → 真上证 5min 复核 E03/E04（消除 510300 代理误差）。
11. 恐慌/割肉盘口语义（E12 买早：253 05-28 vs Wolf 06-05）；1min 可拉，逐笔/委托盘口需前向采集。
12. 大盘量能/券商延续/防御分流环境门 + 个股"站稳趋势线才加仓"触发（E05 300499；日线可先做回测）。
13. 公告/news/涨价事件源（E05/E11/E12/E13）；2025-08 前更早分钟（如需）。
14. 复盘认知/数据收尾（历史新闻/研报/README）——待三仓档位观察定稿后推进。

### 时间型观察（不是开发缺口）
做T 三档触发质量（2-4 周）；P2 Gate 命中日志（2 周）；macro 开关实盘一致性；P3 dry-run 记录；前向日志积累。

## 4. 当前状态（2026-09-03，切换上下文）

- 主线✅ / 浪型✅ / 高低位✅ / 确定性✅ / 做T✅ / 轮动+拥挤PIT✅ / 风控✅ / 宏观 v2✅(历史关键时点已回填) / P2 Gate Step1+2✅(观察) / 日历类✅ / 买点一致率 **v3=92.9%（12 事件 56 行）**。
- 关键结论（语料实证，保持不变）：
  ① Wolf 买点多数不是 254 式盘口触发，是逻辑/分步/底仓回补 → 同日对齐低，但触发一致时收益一致；
  ② 系统与 Wolf 差距=结构性（浪型期加厚底仓、无底仓无T资格、ETF通道、日历/事件源），不是盘口精度；
  ③ 技术硬门槛是旧栈误加，已软约束；
  ④ 拥挤过滤必须个股级 PIT；
  ⑤ 宏观/机构模块按"开关"使用，不是择时器；
  ⑥ E13 缺动作不是宏观漏判（07-08 当日 GJD 护盘尚未在份额显现，07-23 才确认）；缺的是公告/事件源与候选决策留档（strategy_history 已有 07-08=黄色谨慎 trades=[]）。
- 口径注意：v3 的 E01/E02=intent_open 假设、E03/E04 用 510300 代理 253 时点、E04 动作 10-30 vs Wolf 原文 10-29 差 1 日、E15 未并入（用户决定）。

## 5. 关键文档/产物索引（新增部分加粗）

- **买点一致率 v3**：apps/main_line/{judge_wolf_event_alignment.py(ALIGN_V3_EVENTS), backtest_wolf_253_stepwise.py, backtest_wolf_extra_events.py}；data/wolf_event_alignment_v3.json + stepwise_253_backtest_B_extra.json；docs/buy-point-alignment-progress.md（v3 主口径）
- **分钟补拉/数据底座**：apps/main_line/backfill_minute_windows.py；data/stock_5m_bt/（37 文件）；data/strategy_history/
- **macro 历史回填**：apps/main_line/backfill_macro_state_history.py / backtest_macro_wolf.py(history-first)；data/macro_state_history.json；docs/p2-macro-state-backfill.md
- **日历**：apps/main_line/build_earnings_calendar.py；data/earnings_calendar.json；config/industry_rhythm_calendar.json + policy_calendar.json
- **P2 宏观**：docs/p2-macro-wolf-logic.md；apps/main_line/build_macro_state.py；data/macro_state.json
- **P2 Gate**：docs/p2-trading-integration-design.md；backend/app/services/p2_entry_gate.py；config/p2_macro_direction_map.json；data/p2_gate_log.jsonl
- **拥挤 PIT/轮动**：docs/p2-rotation-quadrant-pit-report.md、docs/crowding-stocklevel-event-recheck.md；apps/main_line/{build_fund_pit,backtest_crowding_stock_level,backtest_rotation_quadrant_pit}.py
- **做T**：docs/t-monitor-integration.md；wave_agent/TMonitor 腿；data/wave_state.json
- **缺口与理由事件**：docs/data-gap-inventory.md；data/wolf_reason_events.json；docs/wolf-buy-context-gaps.md
- 既有索引：mainline-oos-validation / wolf-consistency-v2 / p3-three-tier-* / switch-stock-production 等
