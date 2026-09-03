# 逐事件买点一致率 — 进度总表（v2 口径，2026-09-03）

> 目标：提高与 Wolf 的逐事件买点一致率；本文件是事实源汇总，含评测口径、通道落地状态、已否定方案、阻塞项与下一步。
> 关联：docs/buy-point-alignment-metric.md（细则）、docs/tech-entry-system-backtest-report.md、docs/p3-three-tier-*.md、data/wolf_event_alignment_v2.json

## 1. 对外主口径（v2）

- 可测事件：8 个（E05/E06/E08/E09/E10/E11/E12/E13），39 行代理股（5min；E09 用前交易日 02-27）。
- 同日 7/39=**17.9%**；±5 日 29/39=**74.4%**；7/8 事件 aligned（avg 75%）。
- 唯一例外：**E10 主线内切换 0/5 partial** → 缺口 = rotation 切换下沉个股级。
- 上界说明：v2 数字假设“已有底仓且 P3 放行”；E06（exit 无底仓）现实口径应为 0/3，已计入敏感性。

## 2. 已落地/已启用

| 项 | 状态 | 证据 |
|---|---|---|
| P3 三仓档位 + probe≤3%（P3_TIER_MODE=1） | ✅ 生产启用 | commit b9786c2；线上 new_base 硬拦/probe 放行 |
| 统一评测 v2 + 报数纪律 | ✅ | judge_wolf_event_alignment.py、metric.md §7-§10 |
| 253 全护栏 + 254后3日分步回补（5min 回测 v0，A/B） | ✅ 回测完成，生产未接 | backtest_wolf_253_stepwise.py；B：E08 同日 6/6 |
| buy_point_log（候选池/长期池实际买入日志） | ✅ 生产上线 | backend/app/services/buy_point_log.py |
| E09/E10/E11 5min 数据补拉 | ✅ | fetch_brze_extra_5min.py（28 只） |

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
| 数据/源 | E01-E04 5min（2025-11-24 前） | 数据窗 |
| 数据/源 | E14/E15 完整代理篮子与 08-12 后 5min | 数据窗/无篮子 |
| 数据/源 | 公告 AI（anns 403）/业绩/产业事件日历 | 外部源 |
| 离线可做 | 主线内切换下沉个股级（E10 缺口） | 需 rotation 个股级回放基建（工作量） |

## 5. 下一步排序（目标未完成，继续推进）
1. 主线内切换下沉个股级（消掉 E10 唯一 partial）。
2. buy_point_log 积累后把 E01-E15 剩余事件纳入实盘口径。
3. 用户确认后：253 B/分步小仓生产、P3 intent 闭环、ETF 通道。
4. 外部源恢复后：公告/业绩/产业日历驱动买点。
