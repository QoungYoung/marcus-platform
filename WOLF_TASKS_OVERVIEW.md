# 狼大交易策略复制 — 任务总览（大周期 / 小周期 / 已完成）

> 生成：2026-09-01；更新：2026-09-03 晚（用户切换上下文前快照，HEAD commit a86fcee）
> 目标：逆向复刻狼大(-阿狼-)完整 A 股交易策略。
> 已完成：主线判定、浪型v6、高低位v2、确定性门槛、做T体系、主线内轮动/细分宇宙、P2 风控主体、**P2 宏观 v1/v2（观察校准中）**、**P2 Gate 统一接入交易（Step1/2）**。

## 1. 已完成部分

### 1.1 主线判定（main_line_judge）✅
- fusion_mainline 生产权重 (catalyst=0, fund=0.3, rel=0.2, conc=0.5)；研报 catalyst 已被语料审计归零。
- 22 标注日期重放 81%；OOS 5/7=71%（docs/mainline-oos-validation-report.md）；主线历史按日期取已回退（伤精度）。

### 1.2 浪型级别判定（wave_agent）✅ 冻结 v6
- 两级 schema（level/sub_level/operation）；gate：defense/exit 硬拦不建仓；wave_context 接 agent 输出，缺文件回退 rule-based。
- 回测方向 75%/级别族 83%；调度周一 8:10。

### 1.3 高低位分类（position_class）✅ 核心+共振（v2 79%）
- 结构/相对主线/dip_buy 语义拆解；一致性链路 v1 18/18、v2 79%（真实水平）。

### 1.4 确定性门槛（confirm_chain）✅ P1
- S1-S4 + F1-F4；指数确认 60d hit 1.00。

### 1.5 做T体系（三档+黄线+底仓保护）✅ 2026-09-02
- 正T语义修正（大盘带下来=个股被拖累盘中低点）；5min 指数急杀 253 / 个股触前低缩量 254；分时T出 250；黄线跌破离场 252；T1缩转放自动买腿暂缓（无预测力）。
- 底仓保护：做T标的卖出保留 100 底仓（代码硬拦）；t_monitor 5 条持续腿（249/250/252/253/254）。

### 1.6 买点对齐狼大（2026-09-03）
- 误加逻辑审计：MA5>MA20 / 60分MA10>MA30 / KDJ/RSI/CCI/射击之星 / 午后禁开仓 均非 Wolf 语料 → 默认软约束（LEGACY_TECH_GATES=1 可回退）；拥挤核心硬拦保留为风控分歧。
- A/B（E01-E15 技术门）：MA5<MA20 最伤对齐（Wolf MA20下方低吸 T+5 +4.31%）；254同日对齐 1/24、并入253 7/24≈29%；平均买点偏移约 6 交易日。

### 1.7 P2 主线内轮动/产业链形态 ✅ 第一轮闭环 + 拥挤个股级 PIT v2
- rotation_gate v2 + trade_graph 轮动门控 + LOW 候选过滤。
- 拥挤过滤下沉：build_fund_pit（历史 fund_share T-1 top60 + ann_date + top10 清洗）→ crowding_blacklist 从整概念 1045 只 → 公募核心拥挤 17 只（per-symbol reason + 空间豁免）；E06-E13 前向重测验证（E10 国算/E11 材料/E12 低吸不再误拦）。
- 材料大级别买点 / 双维历史回测 / 拥挤硬过滤 3 遗留项全部完成。

### 1.8 P2 风控主体 ✅（2026-09-03）
- risk_gate + risk_flags DB（forecast/express/ST）+ check_entry_filters 硬拦 + trade_graph 风控 prompt + systemic_risk（银行双头/科创50/大光 15:05）。
- 剩余：公告类 source=ai（anns 403，等 news/公告 AI）。

### 1.9 P2 宏观/机构行为 ✅ v1/v2（2026-09-03，观察校准中）
- 语料分析：Wolf 用法 = 4 类开关（崩盘清单/流动性/两融热钱/杀杠杆 + GJD政策底/护盘 + 政策日历 + 龙虎榜外资）。
- macro_state v1 采集器（工作日 15:06）：akshare bond_zh_us_rate（CN/US 2/5/10/30Y，1990 起）+ 新浪 DXY + tushare 两融/GJD宽基/北向。
- macro_state v2：Wolf 四类开关推导（margin_burst/lhb_foreign_sell/gjd_withdraw/yield_spike 等）→ trade_graph 宏观上下文。
- A/B：10/10（两融热钱/杀杠杆/GJD护盘/龙虎榜外资方向全部对上语料；M04 靠 margin_net_buy<=-100 修复；M10 靠 tushare top_list+top_inst 深股通专用净卖修复）。

### 1.10 P2 Gate 统一接入交易 ✅ Step1+Step2（2026-09-03）
- backend/app/services/p2_entry_gate.py：wave defense/exit、systemic level>=2、macro margin_burst → 硬拦；lhb_foreign_sell 方向感知（config/p2_macro_direction_map.json）；gjd_withdraw/yield_spike → 软降。
- 接入 check_entry_filters → auto 通道（candidate/long_term monitor）获得 wave/systemic/macro 硬拦（此前只有 Pi/trade_graph 有 wave 硬拦）。
- p2_gate_log.jsonl + p2_gate_daily_report 任务（15:20，便于 2 周观察校准）。

### 1.11 rotation 13 时点真 PIT 重跑 ✅（2026-09-03）
- 13 个双周时点逐点生成 PIT 拥挤快照（fund_share T-1 top60 + ann_date<=时点 + 每基金 top10）；concept 白名单（SUB_UNIVERSE×stock_concept_map）聚合后重跑双维分类。
- 结果：总相邻转换率仍 **23.4%**（36/154），但 51/180=28% 格子与季度近似口径不同（一致性 71.7%）；修正 04-09 季报前视误标、07-22 起 AI应用/国算/光通信→可埋伏（拥挤切向存储/芯片/材料）。
- 脚本 apps/main_line/backtest_rotation_quadrant_pit.py；报告 docs/p2-rotation-quadrant-pit-report.md；数据 rotation_quadrant_history_pit.json + crowding_pit/stock_crowd_<13时点>.json。

## 2. 生产架构（2026-09-03，tasks=28）

- stock 账户：药明康德底仓 100 股；做T 5 条持续腿；auto_trade 5 任务 enabled。
- 调度：周一 8:00 main_line / 8:10 wave / 8:20 position(链)；周日 8:00 stock_pool / 8:05 宇宙分类；季度 fund_crowding；工作日 15:05 systemic、15:06 macro_state、15:20 p2_gate_daily_report。
- 候选拦截链（所有新开仓通道统一过 check_entry_filters）：
  risk_flags(业绩/ST) → crowding_blacklist(个股级公募核心拥挤) → P2 Gate(wave/systemic/macro) → 技术/资金/形态（Wolf 对齐软约束）→ calc_position。
- 回退开关：LEGACY_TECH_GATES=1（恢复旧技术硬门槛）；P2_GATE_MODE=0（P2 Gate dry-run）。
- 数据文件：data/macro_state.json、data/p2_gate_log.jsonl、data/crowding_pit/*、data/wolf_tech_entries_stockmap.json。

## 3. 任务清单（剩余）

### P2（或 P1 挂起项）
1. P2 宏观收尾（观察型）：DXY 历史源自建快照；崩盘清单缺失源（期指空单/30Y 放量/券商破位）；板块级外资/龙虎榜扩充（覆盖红利核心大票）；policy_floor 事件日历；2 周 p2_gate_log 观察后校准阈值。
2. P2 主线剩余：科技子类粒度（AI硬 vs 半导体）+ 机器人/互金主题覆盖（依赖 P1 标注，用户曾暂缓）。
3. P2 风控剩余：公告类立案/重组/监管 source=ai（anns 403，等 news）。
4. P2 Gate Step3：观察 2 周 → 校准 → 定稿 P2_GATE_MODE=1。

### P3
- 三仓档位模型（底仓/T仓/现金 × 浪型档位）——与"Wolf t_only/defense 加厚底仓"缺口直接相关。
- 复盘认知、数据收尾（历史新闻/研报/README）。

## 4. 当前状态（2026-09-03 晚）

- 主线✅ / 浪型✅ / 高低位✅ / 确定性✅ / 做T✅ / 轮动+拥挤PIT✅（含13时点真PIT重跑✅）/ 风控✅ / 宏观 v2✅（观察）/ P2 Gate 接入✅（Step1+2，观察）。
- 观察窗口：做T 三档触发质量（2-4 周）；P2 Gate 命中日志（2 周）；macro 开关实盘一致性。
- 关键结论（语料实证）：
  ① Wolf 买点多数不是 254 式盘口触发，是逻辑/分步/底仓回补 → 同日对齐低，但触发一致时收益一致；
  ② 系统与 Wolf 差距=结构性（浪型期加厚底仓、无底仓无T资格、ETF通道、日历/事件源），不是盘口精度；
  ③ 技术硬门槛 MA5>MA20/KDJ/RSI/CCI/射击之星/午后禁开 是旧栈误加，已软约束；
  ④ 拥挤过滤必须个股级 PIT，整概念会误拦 E10-E12 类轻仓低吸；
  ⑤ 宏观/机构模块按"开关"使用，不是择时器。

## 5. 关键文档/产物索引（新增部分加粗）

- **P2 宏观**：docs/p2-macro-wolf-logic.md；apps/main_line/build_macro_state.py / backtest_macro_wolf.py；data/macro_state.json；data/macro_source_probe*.json
- **P2 接入交易**：docs/p2-trading-integration-design.md；backend/app/services/p2_entry_gate.py；config/p2_macro_direction_map.json；jobs/p2_gate_daily_report.py；data/p2_gate_log.jsonl
- **13时点真PIT重跑**：docs/p2-rotation-quadrant-pit-report.md；apps/main_line/backtest_rotation_quadrant_pit.py；data/rotation_quadrant_history_pit.json
- **拥挤 PIT**：docs/crowding-stocklevel-event-recheck.md；apps/main_line/{build_fund_pit,backtest_crowding_stock_level}.py；data/crowding_pit/*
- **买点对齐/审计**：docs/wolf-extra-logic-audit.md；docs/ab-wolf-gates-e01e15.md；docs/wolf-buy-context-gaps.md；docs/wolf-dip254-5m-replay.md；docs/wolf-dip254-base-backtest.md；docs/tech-entry-system-backtest-report.md
- 既有索引：mainline-oos-validation / wolf-consistency-v2 / t-monitor-integration / p2-rotation-* / p2-risk-wolf-logic 等