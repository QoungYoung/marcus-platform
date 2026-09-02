# P2 轮动 Case 验证报告（position 回放完成 + wave_agent 后台重放中）

> 生成：2026-09-02 14:55。语料底稿 docs/p2-rotation-cases.md；工具 apps/main_line/validate_rotation_cases.py + merge_rotation_validation.py。
> 状态：position_class 时点回放已 ✅ 完成（data/rotation_position_replay.json）；wave_agent 历史重放 ⏳ 后台运行中（23 unique 日期，单次 dsh 实测 >5 分钟，服务器 marcus-worker 后台任务进行中，逐日写 data/wave_state_<date>.json，最终输出 data/rotation_wave_replay.json）。

## 1. Position 时点回放结论（关键 case）

| Case | 日期 | 概念回放事实 | 与语料预期对照 |
|---|---|---|---|
| B4 | 2026-05-08 | 半导体概念 HIGH(t_only)+净流出-247亿/连续2日；PCB HIGH+流出 | ✅ 顶分型→撤高切低：价格+资金双确认 |
| B1/B2 | 2026-01-12 | 卫星互联网 HIGH(箱顶100)；商业航天 HIGH+流出 | ✅ 高位龙头区，符合死则跑/不低切 |
| C1 | 2026-04-15 | 创新药/减肥药 自身 HIGH(箱顶98-100%) 但 rel_mainline=low(B反)；存储芯片 HIGH+流出-83亿 | ⚠️ 狼大"低位"=G2 相对主线落后(rel=low)，不是自身价格低位——保留相对低位豁免分支 |
| A6 | 2026-08-12 | 创新药 MID、rel=low、B反、流出 | ⚠️ 未到 LOW/确认——对应药还没突破三乌鸦 |
| A4 | 2026-05-25 | 存储芯片 HIGH(箱顶100)+净流入160亿 | 主线高位仍吸金=不该切出 |
| A5 | 2026-08-05 | 半导体概念 MID(vs1y -26.6%)、结构=新高回落、rel=high | 概念级无法分设备/材料子环节，R1 需下沉个股级 |

**Position 侧核心发现**：①C1 证明"低位"是相对主线落后语义；②B4 高位+资金连流出2日即可作 R5 切出信号（不必等 M 顶结构）；③A4/A6 说明切低需"相对低位+资金确认+结构反转"三条件。

## 2. Wave 已知 agent 结果（复用 wave_backtest_result.json）

| 日期 | level/sub/op | 参照 case |
|---|---|---|
| 2025-02-20 | d3/3-1/side | A1 |
| 2025-03-14 | d3/3-3/build | A2 |
| 2025-04-09 | d2/C杀/side | D2 语境 |
| 2025-05-09 | d3/3-1/build | - |
| 2025-10-19 | d3/3-4/t_only | - |

## 3. Wave 23 日期重放清单（后台进行中）

E3=2016-06-24；E4=2022-10-31；A1/E1=2025-02-06/07-11；A2/A3=2025-03-19/2026-01-05；B1/B2/D1=2026-01-12/01-26；B3=2026-03-19；D2/C1/C2/C3/D3/E2=2026-04-14~05-11；A4/B4/B5/D4/D5=2026-05-08/05-25/05-26；A6/D6=2026-08-03/08-12；A5=2026-08-05；C4/C5/C6=2026-07-23/07-27/07-28。

## 4. 下一步
wave 后台完成后跑 merge_rotation_validation.py 出自动对照表；MISMATCH 日期按 case 语境复核（重点 C1/D3 的 t_only/side、A4/D4 的 build）。
