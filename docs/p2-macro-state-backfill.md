# P2 宏观 state 历史回填（关键时点）+ 一致率回测验证

> 2026-09-03 · 目的：E13/E14（政策底/机构）等需要"当时可见"的宏观/机构行为快照做理由门控验证。
> 用户口径：**只回填关键时点**，不逐日全量；回填后**跑回测验证一致率**。
> 配套：docs/data-gap-inventory.md（历史 macro_state 行已勾除）、scripts/build_wolf_reason_events.py、docs/p2-macro-wolf-logic.md。

## 1. 回填范围：19 个关键时点 = Wolf 事件 E05-E15 ∪ 宏观表态 M01-M10

| 日期 | 所属 | 日期 | 所属 |
|---|---|---|---|
| 2025-12-09 | E05 | 2026-03-18 | M10 |
| 2025-12-31 | E06 | 2026-03-19 | E10 |
| 2026-01-05 | E07 | 2026-04-22 | E11 |
| 2026-01-12 | M01 | 2026-06-05 | E12 |
| 2026-01-14 | E08+M02 | 2026-07-08 | E13 |
| 2026-01-16 | M03 | 2026-07-23 | E14+M05 |
| 2026-01-20 | M04 | 2026-07-28 | M09 |
| 2026-02-28 | E09(周六, 数据=02-27) | 2026-07-31 | M06 |
| 2026-03-02 | M08 | 2026-08-03 | M07 |
|  |  | 2026-08-12 | E15 |

事件日期以 data/wolf_reason_events.json 为权威（脚本自动读取）；M01-M10 以 backtest_macro_wolf.LABELS 为权威。
E01-E04 无 2025-11-24 前分钟数据窗口，宏观回填对事件一致率验证意义低，不纳入（符合"只填关键时点"）。

## 2. 方法与数据源（全部按 as-of = 当日"已发布"数据，无未来函数）

- 快照结构与每日 15:06 采集器 data/macro_state.json 同构：yields / dxy / market / macro_switches(+text)。
- **收益率**：akshare bond_zh_us_rate，取 `日期 <= as-of` 的最近一期（CN/US 2/5/10/30Y + 10-2Y），1d 变化=与上一期之差；周末 as-of 自动落到前一交易日。
- **两融/GJD/北向/指数**：tushare 全部 `end_date=as-of` 回看 —— margin（两融余额/20日变化/当日净买）、fund_share+fund_nav（510300/510050 GJD 宽基份额净申赎/近5日流入）、moneyflow_hsgt（北向5日）、index_daily/dailybasic。
- **龙虎榜外资**：top_list+top_inst 当日 exalter=股通专用 净额（与 backtest_macro_wolf 同口径）。
- **开关推导**：复用 build_macro_state._derive_switches（同源，避免两套阈值漂移）。
- **DXY**：新浪仅实时、FRED/Yahoo 不可达 → 历史快照置空，不参与开关推导（P2 收尾遗留项不变）。
- 产物：data/macro_state_history.json（19 日快照，约 52KB）。

## 3. 回测验证一致率：macro A/B（M01-M10）仍 **10/10**

backtest_macro_wolf.py 本次改造：**优先读取 data/macro_state_history.json**（命中则 source=history，不再联网重算），无回填日期才走实时源（source=live）。
服务器重跑结果（data/crowding_pit/macro_wolf_backtest.json）：

| id | 日期 | flags（source=history） | miss |
|---|---|---|---|
| M01 | 2026-01-12 | margin_heat, north_in | - |
| M02 | 2026-01-14 | gjd_withdraw, margin_heat, north_in | - |
| M03 | 2026-01-16 | gjd_withdraw, margin_heat, north_in | - |
| M04 | 2026-01-20 | gjd_withdraw, lhb_foreign_sell, margin_burst | - |
| M05 | 2026-07-23 | gjd_support, margin_burst, north_in | - |
| M06 | 2026-07-31 | gjd_support, margin_burst, north_in | - |
| M07 | 2026-08-03 | gjd_support, margin_burst, north_in | - |
| M08 | 2026-03-02 | gjd_withdraw, north_in | - |
| M09 | 2026-07-28 | gjd_support, margin_burst, north_in | - |
| M10 | 2026-03-18 | gjd_withdraw, lhb_foreign_sell | - |

**AGG ok 10 / 10**，与 09-03 服务器即时源回测结果逐条一致 → 回填快照可完整复现线上 macro v2 判定。

## 4. 事件关键时点宏观总表（E05-E15，data/macro_state_history.json states）

| as-of | 事件 | 开关 flags | 两融净买(亿) | sh300 近5日流入(亿) | sh300 chg20% | sh50 chg20% | cn30Y | us10Y |
|---|---|---|---|---|---|---|---|---|
| 2025-12-09 | E05 | gjd_support, north_in | +87.8 | 见快照 | +0.79 | +0.22 | 2.25 | 4.18 |
| 2025-12-31 | E06 | lhb_foreign_sell, margin_burst | -159.7 | - | -1.1 | -0.98 | 2.27 | 4.18 |
| 2026-01-05 | E07 | margin_heat, north_in | +177.8 | - | -0.94 | -1.35 | 2.28 | 4.17 |
| 2026-01-14 | E08+M02 | gjd_withdraw, margin_heat, north_in | +136.0 | - | -1.71 | -2.90 | 2.30 | 4.15 |
| 2026-02-28 | E09 | gjd_withdraw, north_in | +6.2 | - | -33.45 | -42.48 | 2.27 | (缺) |
| 2026-03-19 | E10 | gjd_withdraw, north_in | -55.8 | - | -4.69 | -4.43 | 2.39 | 4.25 |
| 2026-04-22 | E11 | gjd_withdraw, margin_heat, north_in | +126.2 | - | -8.44 | -8.78 | 2.21 | 4.30 |
| 2026-06-05 | E12 | margin_burst, gjd_withdraw, north_in | -152.4 | sh300 -2.5 / sh50 -4.4 | -20.88 | -45.65 | 2.20 | 4.55 |
| 2026-07-08 | E13 | margin_burst, gjd_withdraw, north_in | -188.6 | sh300 **-57.2** / sh50 +6.2 | **-39.28** | -13.50 | 2.25 | 4.56 |
| 2026-07-23 | E14+M05 | gjd_support, margin_burst, north_in | -97.2 | sh300 **+206.9** / sh50 -9.9 | **+15.82** | +21.05 | 2.20 | 4.71 |
| 2026-08-12 | E15 | margin_burst, gjd_withdraw, lhb_foreign_sell | +94.8 | sh300 -84.1 / sh50 -4.4 | +31.38 | -3.52 | 2.17 | 4.68 |

（两融/份额单位口径与 macro_state.json 一致：净买/流入=亿，chg=%；近5日流入列仅对 E12-E15 展示供解读，其余日期完整值见 states.<date>.market.gjd.sh300_inflow_5d；更多字段：margin_rzrqye、north_5d、lhb.top_sell 等见 states。）

## 5. E12/E13/E14 "政策底/GJD"解读（观察性结论，非调参）

P2 Gate 语义（2026-09-03 接入）：margin_burst=**硬拦新仓**；gjd_withdraw=软降 ×0.5；lhb_foreign_sell=方向感知 ×0.5；yield_spike=软降 ×0.5。

1. **E13（2026-07-08，加仓国产算力）**：当日 flags = margin_burst + gjd_withdraw。
   - GJD 在 20 日口径仍是撤退/减仓（sh300 chg20 **-39.3%**、近5日净流出约 **57 亿**），份额上"护盘"尚未出现；真正的 GJD 大幅申购（sh300 近5日 +207 亿、chg5 +22%）在 **07-23 才显现**，与 Wolf E14 原话"因为 GJD 开始兜底政策底"时间吻合。
   - 即：**E13 当天宏观数据本身不足以确认"政策底已坐实"**——Wolf 的加仓是基于政策/产业计划的预期操作（继续加厚已有国算方向），非新开仓追高。当日若按 P2 Gate：margin_burst 硬拦新仓、gjd_withdraw 软降 0.5，与"等 GJD 兜底确认、不抢新仓"一致。
   - 结论：E13 事件行 ±5 的 3 个未对齐动作**不是宏观开关漏判**（数据侧当日无护盘信号），更可能是动作通道/时间差；政策底"预期入场"代理（政策日历 + 5日短窗口份额反转）若要上，属 P2 Gate Step3 校准范畴。

2. **E14（2026-07-23，4-3 调仓/ETF 为主）**：flags = **gjd_support + margin_burst**。
   - "护盘出现但资金面仍在杀杠杆"的组合，正是 Wolf 语料"上升浪大4回调、4-4 不要预期太高、以 ETF 为主/GJD 画线做指数 T"的背景——开关组合与 Wolf 阶段判断自洽。

3. **E12（2026-06-05，砍光 45→18、慢慢低吸半导体）**：当日 flags = margin_burst（两融净卖 -152 亿）+ gjd_withdraw。
   - 若当日按硬拦口径，"低吸新仓"会被 margin_burst 拦下；而 Wolf 语义是**先切换释放资金、再分批慢慢低吸**——即"卖出→换入"的通道语义与"新建仓"不同。系统真实事件对齐 6/6（within5 覆盖率 100%）是在 P2 Gate 接入(09-03)前完成的；接入后 E12 式当日换仓是否会误拦，列入 **2 周 p2_gate_log 观察点**。

4. **E05（2025-12-09）**：gjd_support + north_in，12 月上旬宽基份额仍在净增，与 Wolf"大盘缩量只加深液冷、等券商回补支撑"偏谨慎的大环境描述不冲突（开关=中性偏支持，未触发任何禁买 flag）。

## 6. 结论

- **回填质量**：19 个关键时点全部按 as-of 可见数据生成；macro A/B 一致率 **10/10 维持**（由回填文件复现，source=history），无数据漂移。
- **事件侧**：E13 的政策底解释成立——GJD 护盘信号直到 07-23 才在宽基份额上出现；E13 缺动作不是漏判宏观。E14 的 gjd_support+margin_burst 组合与 Wolf 4-4/ETF 阶段判断自洽。
- **下一步候选（不阻塞）**：① P2 Gate 对"新建仓 vs 切换/换仓"通道语义的 Step3 观察；② 政策日历 + 5日份额短窗口做"政策底预期"代理（如需）；③ DXY 历史快照、崩盘清单缺失源仍为 P2 宏观收尾遗留。

## 7. 产物与复现

- 脚本：apps/main_line/backfill_macro_state_history.py（新增；--dates 可指定、断点续跑、--force 重算）
- 改造：apps/main_line/backtest_macro_wolf.py（优先读回填文件，source=history/live）
- 数据：data/macro_state_history.json（服务器 /opt/marcus-platform/data + 本仓 data/ 副本，gitignore）
- 结果：data/crowding_pit/macro_wolf_backtest.json（10 行全部 source=history）
- 复现命令：
  - `python -u apps/main_line/backfill_macro_state_history.py`
  - `python -u apps/main_line/backtest_macro_wolf.py`
- 注意：worker 容器内存上限 512MB（常驻已 ~360MB），回填另起 python 曾触发 OOM；本次用 `docker update --memory 1024m marcus-worker` 临时扩容跑完后已还原，脚本已支持增量落盘/断点续跑以降低峰值风险。
