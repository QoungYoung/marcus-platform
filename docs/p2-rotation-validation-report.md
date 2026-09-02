# P2 轮动 Case 验证报告（wave_agent 历史重放 + position_class 时点回放）

> 生成：2026-09-02（晚）。依据 docs/p2-rotation-cases.md 22+ 例；wave=wave_agent(date) 两级判定；position=concept_hist 按日期切片回放。

| ID | 日期 | 组 | 期望wave | 实际level/sub/op | 判定 | 相关概念position回放 |
|---|---|---|---|---|---|---|
| A1 | 2025-02-06 | A | side | d2/W底/side | OK |  |
| A2 | 2025-03-19 | A | build | d3/3-1/build | OK |  |
| A3 | 2026-01-05 | A | build | d3/3-3/build | OK |  |
| A4 | 2026-05-25 | A | build | -/-/- | no-data | 存储芯片=HIGH(in,flat) |
| A5 | 2026-08-05 | A | mixed | d2/ABC/side | na | 半导体概念=MID(in,新高回落); 第四代半导体=MID(in,新高回落) |
| A6 | 2026-08-12 | A | tside | -/-/- | no-data | 创新药=MID(out,B反) |
| B1 | 2026-01-12 | B | build | d3/3-3/build | OK | 卫星互联网=HIGH(in,flat) || 商业航天=HIGH(out,flat) |
| B2 | 2026-01-12 | B | build | d3/3-3/build | OK | 商业航天=HIGH(out,flat) |
| B3 | 2026-03-19 | B | tside | d4/4-1/defense | MISMATCH |  |
| B4 | 2026-05-08 | B | build | d3/3-3/build | OK | 半导体概念=HIGH(out,flat); 第四代半导体=HIGH(out,B反) || PCB=HIGH(out,flat) |
| B5 | 2026-05-08 | B | build | d3/3-3/build | OK |  |
| C1 | 2026-04-15 | C | tside | d3/3-4/t_only | OK | 创新药=HIGH(in,B反) || 减肥药=HIGH(in,B反) || 中药概念=MID(in,B反) |
| C2 | 2026-04-16 | C | tside | -/-/- | no-data |  |
| C3 | 2026-05-11 | C | na | d3/3-3/build | na |  |
| C4 | 2026-07-23 | C | tside | -/-/- | no-data |  |
| C5 | 2026-07-27 | C | tside | d2/B反/side | OK |  |
| C6 | 2026-07-28 | C | tside | -/-/- | no-data |  |
| D1 | 2026-01-26 | D | na | d3/3-3/build | na |  |
| D2 | 2026-04-14 | D | tside | d3/3-4/t_only | OK |  |
| D3 | 2026-04-15 | D | tside | d3/3-4/t_only | OK |  |
| D4 | 2026-05-25 | D | build | -/-/- | no-data |  |
| D5 | 2026-05-26 | D | t_only | d3/3-4/t_only | OK |  |
| D6 | 2026-08-03 | D | tside | d2/C杀/side | OK |  |
| E1 | 2025-07-11 | E | na | d3/3-1/build | na |  |
| E2 | 2026-04-23 | E | na | -/-/- | na |  |
| E3 | 2016-06-24 | E | na | d1/W底/side | na |  |
| E4 | 2022-10-31 | E | na | down/C杀/defense | na |  |

## gate 语义（按实际 wave op）

- build → 主升/主浪→只做主线内细分轮动, 禁切出主线(板块级高低切)
- defense → 防御→禁止轮动, 防守为主
- side → 观望/调仓换股→同t_only(可防御切低, 降低随意调仓)
- t_only → 只做T/4-4→允许防御性高切低(候选LOW+无2孕线+未放量破前低)

## 结论要点（供人工裁决）
- 自动判定只做 wave op 与文档预期(op family)对照；position 回放列供 C1/B4/A6 等目标验证。
- OK=符合预期 op family；PARTIAL=可接受但更保守/更进取；MISMATCH=与语料预期相悖，需复核 case 语境或 wave_agent 判定。