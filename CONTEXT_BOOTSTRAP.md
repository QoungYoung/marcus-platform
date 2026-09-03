# 新上下文启动包（狼大策略复制 / 恢复指引）

> 更新：2026-09-04（第三次切换上下文快照；commit 见 git log。本轮完成 Wolf 选股/做T/风控对齐）。

## 0. 先读这几份
1. 仓库根 WOLF_TASKS_OVERVIEW.md —— 任务总览（含最新：买点一致率 v3=92.9%、macro 历史回填、分钟补拉、日历类）。
2. 仓库根 CONTEXT_BOOTSTRAP.md（本文件）。
3. dsh-memoir（memoir_read）：近几日记录多；关键词：crowding PIT、macro_state、P2 Gate、wolf dip、E06-E13、brze、idx_mins、v3、minute。
4. 服务器 /opt/marcus-platform/data/wave_config.md（浪型 v6 冻结）。


## 0. 本轮(2026-09-04)进展 + 下一位开发待办
- **Wolf 逻辑对齐已完成**：主线判定/成分股确认/高低位/confirm_chain/浪型门(wave_level_gate defense/exit→不建仓)/做T(正T+确认T出+倒T+defensive+申万行业防御 defensive_t_reduce_sw)/周末降险/weekend_de_risk/六因子 wolf_judge(软指导)/check_entry_filters(旧技术硬门转软, LEGACY_TECH_GATES=1 回退)/board_half(减半,TMonitor写wolf_board_half_sell)/主线内轮动去弱留强+龙头+相对强度(rotation_gate上下文)/产业链形态(提示词让Pi用get_concept_mapping自查, 非数据注入)/**不再注入震荡/趋势市状态(regime/style_context置空, _get_trade_instruction按浪型(见wave_context)执行)**。
- **下一位开发者待办（明确未做）**：①风控·查杠杆(P2)——个股/持仓级两融·融资盘检查，未进风控链；②宏观机构行为/持仓纪律/复盘(P2/P3)——机构行为仅宏观级，持仓纪律 backend=0，复盘缺完整买卖/持仓纪律闭环。详见 WOLF_TASKS_OVERVIEW.md §0。
## 1. 已完成（不用重做，结论在文档/记忆）
- 主线/浪型/高低位/确定性/做T/轮动+拥挤PIT/风控/P3 三仓档位 v0(dry-run)。
- **P2 宏观 v2 + 历史回填**：macro_state 15:06 采集；Wolf 四类开关；A/B M01-M10=10/10；backfill_macro_state_history.py 已回填 19 关键时点(E05-E15∪M01-M10)→data/macro_state_history.json（E13 07-08 margin_burst+gjd_withdraw，GJD 护盘 07-23 才在份额显现）。
- **P2 Gate Step1+2**：p2_entry_gate 进 check_entry_filters；方向感知 map；p2_gate_log + 15:20 报告。
- **买点一致率 v3**：E01-E04 分钟补拉(brze) + intent_open/stepwise 扩展回放；judge ALIGN_V3_EVENTS → wolf_event_alignment_v3.json：12 事件 56 行、±5 日 52/56=92.9%、12/12 aligned；**E15 按用户决定不并入**（w5 3/7=0.43）。
- 日历类：业绩披露 v2（08:40 刷新）、H1/H2 产业节奏+政策会议 config。
- 拥挤过滤个股级 PIT v2；rotation 13 时点真 PIT 重跑。

## 2. 生产状态（2026-09-03）
- stock 账户：药明底仓 100 股 + SH588170(base-floor 66,900)；做T 5 腿(249/250/252/253/254)；auto_trade 5 任务 enabled；TMonitor 30s。
- 调度 config/tasks.yaml（25 任务条目）：周一主线/浪型/position；周日入池/宇宙；季度基金拥挤；每日 08:40 业绩日历、15:05 systemic、15:06 macro_state、15:20 p2_gate_daily_report。
- 候选拦截链：risk_flags → crowding_blacklist(个股级) → P2 Gate(wave/systemic/macro) → 技术/资金（Wolf 对齐软约束）→ calc_position(P3 dry-run)。
- 回退开关：LEGACY_TECH_GATES=1；P2_GATE_MODE=1（0=dry-run）；P3_TIER_MODE=0。

## 3. 数据源/能力现状（重点）
- **分钟**：brze stk_mins 可用（个股+ETF：5min 48根/日、1min 241根/日，回溯≥2024-01；限制：brze 限频 ≥1s、stk 仅支持股票/ETF）。**brze idx_mins（上证指数分钟）= tenant key expired，需管理员续**；akshare EM 指数 5min 服务器断连 → E01-E04 253 时点暂用 510300.SH 代理（口径差异已标）。
- 现有分钟文件：data/stock_5m_bt/（37 文件：28 代理 + 603296/603986/688008/300475/001309/512480/159995/588200/510300）；index_5min_dh.json（2025-12-01~2026-09-01）；data/strategy_history/（2026-05-25 起逐日 Pi 快照）。
- 收益率：worker akshare bond_zh_us_rate 一表 CN/US 2/5/10/30Y；DXY 实时=新浪，历史待自建；FRED/Yahoo/EM 分钟在服务器不可用。
- 龙虎榜外资：tushare top_list+top_inst（深股通/沪股通专用净额）。
- P2 Gate 日志：data/p2_gate_log.jsonl + 每日聚合 data/p2_gate_daily_report.json。

## 4. 生产注意事项
- 只有狼大做T可操作 stock 账户；做T卖出保留 100 底仓；止损 DYNAMIC_ONLY 只读。
- prompt 权威源 backend/app/db/prompt_seeds.py；改后 reseed。
- tasks.yaml 本地 config/ 权威；改后同步服务器并 restart marcus-worker（跑长任务勿重启）。
- 重启惯例：backend/app|jobs 改动→marcus-worker；backend/api|models→marcus-backend；apps/main_line 脚本→/app/apps bind mount 生效。
- **长任务/后台坑**：经 ssh 启动的容器后台进程约 213s 被回收（非 OOM）→ 长拉取必须拆小批次+断点续跑（例：backfill_macro_state_history/backfill_minute_windows 均按日增量落盘）；docker exec -d 也不能豁免；worker 内存 512MB 上限，大任务临时 docker update --memory 1024m 跑完还原。
- brze/官方源差异：官方 api.tushare.pro 不认代理 token；gyzcloud 代理不透传 stk_mins；分钟只能走 brze。

## 5. 下一步候选（详见 WOLF_TASKS_OVERVIEW §3）
1. 更新快照落盘（本文件/Overview）后，等用户新指令或按排序：brze idx_mins 续 key→真上证复核 E03/E04。
2. P2 Gate Step3：观察 2 周 → 校准 → 定稿。
3. P2 宏观收尾：DXY 历史快照自建、policy_floor 事件日历（可先 config 人工）、崩盘清单源、板块级外资覆盖。
4. P3 intent 闭环生产 / 253 B+分步小仓接 TMonitor / ETF 执行通道（均需用户确认）。
5. 数据层：割肉盘口前向采集（E12 买早）、公告/news/涨价源（403）、大盘环境门+趋势线加仓回测（E05 300499）。
