# 逐事件买点一致率 — 进度总表（v3 口径，2026-09-03）

> 目标：提高与 Wolf 的逐事件买点一致率；本文件是事实源汇总，含评测口径、通道落地状态、已否定方案、阻塞项与下一步。
> 关联：docs/buy-point-alignment-metric.md（细则）、docs/tech-entry-system-backtest-report.md、docs/p3-three-tier-*.md、data/wolf_event_alignment_v3.json（官方）/ v2.json（历史口径）

## 1. 对外主口径（v3 = v2 + E01-E04，2026-09-03）

- 可测事件：**12 个**（E01-E04 + E05/E06/E08/E09/E10/E11/E12/E13），56 行代理股；同日 18/56=**32.1%**；±5 日 **52/56=92.9%**；12/12 事件 aligned（avg 93.1%）。
- E01-E04 口径：E01/E02 = intent_open（开盘建仓/probe≤3% 同日）；E03/E04 = stepwise B（253/254/refill；253 大盘急杀时点=510300 宽基代理）；E04 动作落 10-30、Wolf 原文 10-29 低点买（差 1 交易日，见 caveats）。
- v2 历史口径：8 事件 / 39 行 / 同日 17.9% / ±5 日 35/39=89.7%（E05-E13，保留对比用）。
- E15（08-12 买回半导体）代理池回放 w5=3/7=0.43 → **按用户决定不并入 v3**，维持 no_5m_replay；E07（月度主攻无买点）/E14（调仓准备、ETF 为主）同样不计。
- 上界说明：v3 假设“已有底仓且 P3 放行”；E06（exit 无底仓）现实口径应下调，已计入敏感性。

## 2. 已落地/已启用

| 项 | 状态 | 证据 |
|---|---|---|
| P3 三仓档位 + probe≤3%（P3_TIER_MODE=1） | ✅ 生产启用 | commit b9786c2；线上 new_base 硬拦/probe 放行 |
| 统一评测 v2 + 报数纪律 | ✅ | judge_wolf_event_alignment.py、metric.md §7-§10 |
| 253 全护栏 + 254后3日分步回补（5min 回测 v0，A/B） | ✅ 回测完成，生产未接 | backtest_wolf_253_stepwise.py；B：E08 同日 6/6 |
| buy_point_log（候选池/长期池实际买入日志） | ✅ 生产上线 | backend/app/services/buy_point_log.py |
| E09/E10/E11 5min 数据补拉 | ✅ | fetch_brze_extra_5min.py（28 只） |
| E01-E04/E15 分钟窗口补拉（2025-08~10 / 08-12 后，brze，含 ETF/510300 代理） | ✅ 数据 | backfill_minute_windows.py；stock_5m_bt 28→37 文件 |
| E01-E04 扩展回放 v0（intent_open + stepwise B） | ✅ 回测 | backtest_wolf_extra_events.py → stepwise_253_backtest_B_extra.json（24 行） |
| judge v3（ALIGN_V3_EVENTS=E01,E02,E03,E04） | ✅ 官方口径 | wolf_event_alignment_v3.json：56 行 52/56=92.9%，12/12 事件 |
| 主线内切换下沉个股级（E09-E12 v0.2，r20） | ✅ 回测 | backtest_rotation_switch_stock.py；E09/E10/E11/E12 全部 executed |

## 3. 已实验并否定（不要再做）

- 日线“趋势内加仓”机械触发（backtest_wolf_trend_add.py）：触发 13/24、±5 日仅 3/24，集中在市场性回踩日；Wolf 加仓是日历/逻辑驱动。
- E06 “exit+无底仓放行”：放开仅 +3/3 一致性、sys T+5 ≈ −2.6%（Wolf 也 −4.65%），违背 exit 纪律 → 维持“系统有意分歧”。
- 253 “站回 cumVWAP 才买”（A）：E08 同日 6/6→2/6，偏离“急杀即接”语料 → 推荐 B 语义 + 小仓护栏。

## 4. 未做（按阻塞类型）

| 类型 | 项 | 阻塞 |
|---|---|---|
| 生产需用户确认 | 253 B 语义 + 分步小仓接 TMonitor | 用户明确“先不对接” |
| 生产需用户确认 | P3 intent 闭环（monitors/Pi 真用 refill/add/probe；probe 确认链） | 行为变更 |
| 生产需用户确认 | ETF 执行通道（E08/E14） | 账本/券商支持 |
| 数据/源 | ~~E01-E04 5min（2025-11-24 前）~~ | ✅ 已由 brze 补拉 2025-08~10（backfill_minute_windows.py）；仍缺 2025-08 前（如需更早） |
| 数据/源 | E14 完整代理篮子（调仓准备、ETF 为主） | 无篮子（E15 已拉代理池 5min 但按用户决定不并入 v3） |
| 数据/源 | 公告 AI（anns 403）/业绩/产业事件日历 | 外部源 |
| 生产需用户确认 | 主线内切换生产接线（链级决策+个股选股，dry-run 草案） | docs/switch-stock-production.md |

## 5. 下一步排序（目标未完成，继续推进）
1. 主线内切换下沉个股级（消掉 E10 唯一 partial）。
2. buy_point_log 积累后把 E01-E15 剩余事件纳入实盘口径。
3. 用户确认后：253 B/分步小仓生产、P3 intent 闭环、ETF 通道。
4. 外部源恢复后：公告/业绩/产业日历驱动买点。
