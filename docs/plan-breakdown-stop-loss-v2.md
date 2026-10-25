# 止损/破位开发方案 v2（账本逐条对齐版）

> 目的：把 2026-10-23 的 86 条《狼大止损/减仓发言账本》逐条对到现有代码，重新设计一版**不拿代理冒充原话**的方案。
> 输入：`docs/wolf-stop-loss-ledger.md`（86 条）、`docs/break-rule-provenance.md`（来源审计）、现有 P1-P5a 代码与生产六层止损。
> 本文**不改代码**，只给设计与处置；拍板后再按 §5 落地。

---

## 0. 结论

1. **代码不是“没做”，是“碎片化 + 代理化”**：至少 6 个模块各管一段（`wolf_trend_stop`、`wolf_early_stop`、`wolf_passive_stop`、`t_stop_loss_guard`、`t_index_break`、`t_monitor`），判据分散、优先级靠代码顺序。
2. **最大冲突**：生产仍以**个股级止损**为主（`WOLF_STOP_BY_INDEX_BREAK=0`），而账本的核心是「**只看指数大级别**」（2026-08-27）与「顶多就是指数破位后的止损」（2026-08-25）。这不是参数问题，是**口径问题**。
3. **第二大冲突**：P4 的 `--live` 把盘中现价追加成当日 bar，再按 `phase="close"` 判——**违反“盘中跌破不算 / 收盘确认”**（2025-11-21、2026-01-29）。P4 任务 14:50 跑 `--live` 是错的。
4. **第三大冲突**：P3 自设了 B3-1 / B3-2 / B3-3 的“1 个结构 vs ≥2 根 MA”分层；账本只给了**点位 → 仓位**（3922→40%、3900→20%、3888→50%、3850→35%）和“下跌 1 浪不做”，没有这层阈值。
5. **重新设计的一句话**：**事实层只给事实；三条判据线（指数/趋势/个股）各管一段；动作只认点位表与“不做”；执行只有一个口。**

---

## 1. 账本 → 代码逐条矩阵（86 条）

判定码：
- ✅ 已对齐：代码已有，且口径与账本一致
- ⚠️ 部分：有实现，但阈值/口径是代理，或只覆盖一部分
- ❌ 未实现：账本有、代码没有
- 🔁 冲突：代码行为与账本相反
- 🧪 未启用：新代码有，但默认关/未接线
- 📜 历史：早期口径，勿与现在混读

| # | 日期 | 账本判据（摘要） | 代码/模块 | 判定 | 差距/备注 |
|---:|---|---|---|---|---|
| 1 | 2022-03-07 | 风险优先；安全第一、保命要紧 | risk_gate / position_tier / wave_state | ⚠️ 部分 | 总纲已落在仓位/风控，不是破位判据 |
| 2 | 2022-04-15 | 破位进入 4 浪则全收；不硬抗 | risk_gate / position_tier / wave_state | ⚠️ 部分 | 总纲已落在仓位/风控，不是破位判据 |
| 3 | 2022-04-25 | 4 浪末尾不做；等 5 浪 | risk_gate / position_tier / wave_state | ⚠️ 部分 | 总纲已落在仓位/风控，不是破位判据 |
| 4 | 2025-09-03 | 一次破位可吃掉一个月利润；纪律优先 | risk_gate / position_tier / wave_state | ⚠️ 部分 | 总纲已落在仓位/风控，不是破位判据 |
| 5 | 2025-10-23 | 仓位 45-75%，20% 防御 | risk_gate / position_tier / wave_state | ⚠️ 部分 | 总纲已落在仓位/风控，不是破位判据 |
| 6 | 2026-01-29 | 平台破位=减仓；活下去第一 | t_monitor.custom_support_sell / wolf_passive_stop | ⚠️ 部分 | 有减仓/被动止盈，但无统一平台破位动作 |
| 7 | 2026-02-02 | 亏损根源=仓位失控；错了止损 | risk_gate / position_tier / wave_state | ⚠️ 部分 | 总纲已落在仓位/风控，不是破位判据 |
| 8 | 2026-03-23 | 止损时点门：13:00-14:30 不砍；要么早盘、要么尾盘 | t_monitor._stop_time_ok | ✅ 已对齐 | 13:00-14:30 时点门 |
| 9 | 2026-06-08 | 弱势行情纪律=保命，不是多赚 | risk_gate / position_tier / wave_state | ⚠️ 部分 | 总纲已落在仓位/风控，不是破位判据 |
| 10 | 2021-01-28 | 用最简单的线做止盈止损；止损位=出场位 | wolf_early_stop / wolf_trend_stop / stop_loss_price | ⚠️ 部分 | 多数已落；阈值/口径是代理 |
| 11 | 2021-02-05 | 带量收破 108 才是止损位；109 是补仓位 | stop_loss_price / t_stop_loss_guard | ✅ 已对齐 | 带量收破线→止损；线本身由策略给 |
| 12 | 2021-02-22 | 带量破通道下沿→策略单丢 | wolf_early_stop / wolf_trend_stop / stop_loss_price | ⚠️ 部分 | 多数已落；阈值/口径是代理 |
| 13 | 2022-02-14 | 收盘破 18.1，次日直接割；硬止损 | stop_loss_price / t_stop_loss_guard | ✅ 已对齐 | 硬止损价已落 |
| 14 | 2022-07-29 | 宁德破 510 的 15min 绿K→卖；单票 -5% 止损 | stop_loss_price / t_stop_loss_guard | ✅ 已对齐 | 单票止损价已落 |
| 15 | 2025-01-23 | 波段仓：跌下去不补，超 10% 止损 | wolf_early_stop / wolf_trend_stop / stop_loss_price | ⚠️ 部分 | 多数已落；阈值/口径是代理 |
| 16 | 2025-06-06 | 收盘破 60min 中轨→不留 | t_monitor._check_60min_analysis | ✅ 已对齐 | 60min 中轨 |
| 17 | 2026-01-19 | 仓位失控+破位=跑不出去 | wolf_early_stop / wolf_trend_stop / stop_loss_price | ⚠️ 部分 | 多数已落；阈值/口径是代理 |
| 18 | 2026-03-05 | 建仓初期：13 日 / 波段低点 −3% 未收回→止损 | wolf_early_stop | ✅ 已对齐 | 13 日 / −3% 已落（数据有效性见 audit） |
| 19 | 2026-03-05 | 有效跌破=自己逻辑被证伪；黑天鹅除外 | wolf_early_stop | ✅ 已对齐 | 13 日 / −3% 已落（数据有效性见 audit） |
| 20 | 2026-03-06 | 成趋势后换趋势线法，不是一个策略用到底 | wolf_trend_stop | ✅ 已对齐 | 成趋势后换趋势线法 |
| 21 | 2021-01-28 | 13/34 强弱分类，60 日底线；34 日带量下穿砍 | wolf_trend_stop | ✅ 已对齐 | 13/34/60 已落 |
| 22 | 2021-02-26 | 大盘破通道→斩立决 | wolf_trend_stop / support_resistance / wolf_boll_levels | ⚠️ 部分 | 趋势/BOLL 已落；平台/前高/点位未接线 |
| 23 | 2021-05-14 | 守 144=反弹；不守=破位趋势 | wolf_trend_stop / support_resistance / wolf_boll_levels | ⚠️ 部分 | 趋势/BOLL 已落；平台/前高/点位未接线 |
| 24 | 2022-10-24 | 不到线不动；上不能减、下不能止损 | stop_loss_price / t_stop_loss_guard | ✅ 已对齐 | 止损价不动 |
| 25 | 2025-05-13 | 顶部阶段：支撑=上一天最高/缺口下沿；收盘破→止损 | wolf_trend_stop / support_resistance / wolf_boll_levels | ⚠️ 部分 | 趋势/BOLL 已落；平台/前高/点位未接线 |
| 26 | 2025-05-15 | 收盘跌破日线中轨→清仓（3316） | wolf_boll_levels / WOLF_BOLL_MID_EXIT | ✅ 已对齐 | BOLL 中轨 |
| 27 | 2025-06-23 | 3373 趋势线/BOLL 中轨；3330-3334 M 顶最后防守 | wolf_trend_stop / support_resistance / wolf_boll_levels | ⚠️ 部分 | 趋势/BOLL 已落；平台/前高/点位未接线 |
| 28 | 2025-08-01 | 快速放量下破 3560→直接减半 | wolf_trend_stop / support_resistance / wolf_boll_levels | ⚠️ 部分 | 趋势/BOLL 已落；平台/前高/点位未接线 |
| 29 | 2025-08-14 | 过高出针顶+尾盘放量破趋势→删票 | wolf_trend_stop / support_resistance / wolf_boll_levels | ⚠️ 部分 | 趋势/BOLL 已落；平台/前高/点位未接线 |
| 30 | 2025-09-04 | 2:30 破位止损位=3761，确认跌破者割肉位 | wolf_trend_stop / support_resistance / wolf_boll_levels | ⚠️ 部分 | 趋势/BOLL 已落；平台/前高/点位未接线 |
| 31 | 2025-10-15 | 低点不割，等 3891-3910 反抽再减 | wolf_trend_stop / support_resistance / wolf_boll_levels | ⚠️ 部分 | 趋势/BOLL 已落；平台/前高/点位未接线 |
| 32 | 2026-01-12 | 收黑K跌破 5 日线→减仓避一下（短期见顶） | wolf_trend_stop（reduce） | ✅ 已对齐 | 破 5 日线减仓 |
| 33 | 2026-03-04 | 缩量跌破不算，等反抽不过颈线；放量跌破直接算 | break_detector（P2） | 🧪 未启用 | 缩量/放量分支，未接生产 |
| 34 | 2026-05-06 | 突破后以收盘点为止损；个股以板块指数为准 | wolf_trend_stop / support_resistance / wolf_boll_levels | ⚠️ 部分 | 趋势/BOLL 已落；平台/前高/点位未接线 |
| 35 | 2026-05-21 | 放量跌破前一日红K、量能多一倍→卖点 | break_detector（P2） | 🧪 未启用 | 放量破前日红K，未接生产 |
| 36 | 2025-03-11 | 盘中破 3344 收不回→一键清仓 | t_index_break / t_capacity | ❌ 未实现 | 只有 MA60/144/200；点名点位/点位表未接线 |
| 37 | 2025-03-31 | 3334 收破→多头变空头；3303 多头止损 | t_index_break / t_capacity | ❌ 未实现 | 只有 MA60/144/200；点名点位/点位表未接线 |
| 38 | 2025-05-26 | 收盘破 3334→先出场观望 | t_index_break / t_capacity | ❌ 未实现 | 只有 MA60/144/200；点名点位/点位表未接线 |
| 39 | 2025-06-20 | 收破 3370/3375→减仓；破 3334→反抽全割 | t_index_break / t_capacity | ❌ 未实现 | 只有 MA60/144/200；点名点位/点位表未接线 |
| 40 | 2025-08-28 | 收盘不到 3856→减出来 | t_index_break / t_capacity | ❌ 未实现 | 只有 MA60/144/200；点名点位/点位表未接线 |
| 41 | 2025-09-07 | 3885 站不上→三重顶；放量黑K→减到 30% 内 | t_index_break / t_capacity | ❌ 未实现 | 只有 MA60/144/200；点名点位/点位表未接线 |
| 42 | 2025-09-17 | 3828 放量破+30年债涨+外资流出→降到 50% | t_index_break / t_capacity | ❌ 未实现 | 只有 MA60/144/200；点名点位/点位表未接线 |
| 43 | 2025-10-12 | 跌破 3731 收盘→转阶段确认；剩 30% 等大底 | t_index_break / t_capacity | ❌ 未实现 | 只有 MA60/144/200；点名点位/点位表未接线 |
| 44 | 2025-11-18 | 跌破 3950 支撑→容易二次三次；尾盘不站回 3980-3986 保持 60% | t_index_break / t_capacity | ❌ 未实现 | 只有 MA60/144/200；点名点位/点位表未接线 |
| 45 | 2025-11-21 | 收盘破 3922→40%；破 3900→20%；盘中跌破不算 | t_index_break / t_capacity | 🧪 未启用 | 点位表未接硬闸 |
| 46 | 2025-11-23 | 跌破趋势 3922→趋势仓 10-15%，30% 短线，隔夜≤50% | t_index_break / wolf_context | 🧪 未启用 | 指数破位默认关 |
| 47 | 2025-12-03 | 3888 未站上→50%；3850 破→35%；3816 企稳→50%；3922-3930→70% | t_index_break / t_capacity | 🧪 未启用 | 点位表未接硬闸 |
| 48 | 2025-12-11 | 跌破昨天低点超 3 个 15min 不收回→反抽减仓 | t_index_break / t_capacity | ❌ 未实现 | 只有 MA60/144/200；点名点位/点位表未接线 |
| 49 | 2026-01-14 | 再破 4141 放量+3 个 15min 确认→减仓下调 | t_index_break / t_capacity | ❌ 未实现 | 只有 MA60/144/200；点名点位/点位表未接线 |
| 50 | 2026-01-30 | 跌破 4100，反抽上不了 4126→减到 40% 内 | t_index_break / t_capacity | ❌ 未实现 | 只有 MA60/144/200；点名点位/点位表未接线 |
| 51 | 2026-02-06 | 除非放量跌破 4000 才减仓 | t_index_break / t_capacity | ❌ 未实现 | 只有 MA60/144/200；点名点位/点位表未接线 |
| 52 | 2026-03-20 | 量化第二次跌破 4000→硬止损 | t_index_break / t_capacity | ❌ 未实现 | 只有 MA60/144/200；点名点位/点位表未接线 |
| 53 | 2021-07-27 | 券商破位+创业板收不回→券商全砍，总仓≤30% | position_tier / t_capacity / wolf_discipline | ⚠️ 部分 | 有档位/点位表，但不是破位动作 |
| 54 | 2022-05-19 | 确定性收益比：这里只常驻 30% | position_tier / t_capacity / wolf_discipline | ⚠️ 部分 | 有档位/点位表，但不是破位动作 |
| 55 | 2022-08-01 | 市场破位→必须出来一部分，不能全仓赌 | position_tier / t_capacity / wolf_discipline | ⚠️ 部分 | 有档位/点位表，但不是破位动作 |
| 56 | 2025-04-24 | 今明两天降到 30% 以下 | position_tier / t_capacity / wolf_discipline | ⚠️ 部分 | 有档位/点位表，但不是破位动作 |
| 57 | 2025-04-28 | 有上影线→减到 10% 过节；否则 15% | position_tier / t_capacity / wolf_discipline | ⚠️ 部分 | 有档位/点位表，但不是破位动作 |
| 58 | 2025-06-04 | 仓位控制 50% 以下；盘中 T 尾盘出场 | position_tier / t_capacity / wolf_discipline | ⚠️ 部分 | 有档位/点位表，但不是破位动作 |
| 59 | 2025-07-04 | 除被套 40% 机器人，其他都清 | position_tier / t_capacity / wolf_discipline | ⚠️ 部分 | 有档位/点位表，但不是破位动作 |
| 60 | 2025-09-24 | 长线锁仓，到位置才加减；被动止盈线 | position_tier / t_capacity / wolf_discipline | ⚠️ 部分 | 有档位/点位表，但不是破位动作 |
| 61 | 2026-01-17 | 主升 75%+ / 调整 50% / 有风险 30% / 下跌不做 | position_tier / t_capacity / wolf_discipline | ⚠️ 部分 | 有档位/点位表，但不是破位动作 |
| 62 | 2026-03-02 | 降仓可以，但不能清仓科技 | position_tier / t_capacity / wolf_discipline | ⚠️ 部分 | 有档位/点位表，但不是破位动作 |
| 63 | 2022-05-24 | 收盘前不预判破位；场外预判不算 | t_monitor / t_stop_loss_guard / break_policy | ⚠️ 部分 | 部分在，但盘中/缩量分支不统一 |
| 64 | 2025-06-10 | 3370-3373 没收破就不清仓 | t_monitor / t_stop_loss_guard / break_policy | ⚠️ 部分 | 部分在，但盘中/缩量分支不统一 |
| 65 | 2025-09-05 | 出场=贪；有浮亏正常，别乱割 | t_monitor / t_stop_loss_guard / break_policy | ⚠️ 部分 | 部分在，但盘中/缩量分支不统一 |
| 66 | 2026-01-14 | 急跌不割；等反抽再减 | break_policy（alert） | ⚠️ 部分 | 急跌不割，只有新策略里是 alert |
| 67 | 2026-03-19 | 不在最低点割肉；等反抽减 | break_policy（deep loss alert） | ✅ 已对齐 | 不在最低点割 |
| 68 | 2026-03-24 | 破 144 三天也不在低点割；等能接受的位置 | break_policy（deep loss alert） | ✅ 已对齐 | 破 144 三天也不低点割 |
| 69 | 2026-07-27 | 跌不割、涨不追；做指数差价 | t_monitor / t_stop_loss_guard / break_policy | ⚠️ 部分 | 部分在，但盘中/缩量分支不统一 |
| 70 | 2026-08-03 | 尾盘放量=割肉盘；敢啃（有 3800 支撑） | t_monitor / t_stop_loss_guard / break_policy | ⚠️ 部分 | 部分在，但盘中/缩量分支不统一 |
| 71 | 2026-01-12 | 等收盘确认破位再出清 | t_stop_loss_guard / _stop_close_confirm | ✅ 已对齐 | 收盘确认 |
| 72 | 2026-08-17 | 底仓/T仓分账；底仓不动 | break_policy / break_stop_service | 🧪 未启用 | 新代码，alert/execute_index 未开 |
| 73 | 2026-08-18 | 目标位减底仓，不猜最高 | break_policy / break_stop_service | 🧪 未启用 | 新代码，alert/execute_index 未开 |
| 74 | 2026-08-19 | 止损预设；按计划执行 | break_policy / break_stop_service | 🧪 未启用 | 新代码，alert/execute_index 未开 |
| 75 | 2026-08-25 | 止损以指数破位为准（文档引述） | break_policy / break_stop_service | 🧪 未启用 | 新代码，alert/execute_index 未开 |
| 76 | 2026-08-27 | 指数大级别转下跌1浪→止损（NGA 楼549） | break_policy / break_stop_service | 🧪 未启用 | 新代码，alert/execute_index 未开 |
| 77 | 2026-09-02 | T 目标 3-4%；力度不够也收工 | break_policy / break_stop_service | 🧪 未启用 | 新代码，alert/execute_index 未开 |
| 78 | 2026-09-04 | 尾段不加仓；只做T；最后撤 | break_policy / break_stop_service | 🧪 未启用 | 新代码，alert/execute_index 未开 |
| 79 | 2026-09-17 | 事件驱动降仓：50%→30% | break_policy / break_stop_service | 🧪 未启用 | 新代码，alert/execute_index 未开 |
| 80 | 2026-09-21 | 收盘 50% / 盘中 70% | break_policy / break_stop_service | 🧪 未启用 | 新代码，alert/execute_index 未开 |
| 81 | 2026-09-30 | 洗盘≠趋势变化；不因日内波动改策略 | break_policy / break_stop_service | 🧪 未启用 | 新代码，alert/execute_index 未开 |
| 82 | 2026-10-12 | 安全仓位；等银行/红利放量大黑K | break_policy / break_stop_service | 🧪 未启用 | 新代码，alert/execute_index 未开 |
| 83 | 2026-10-15 | 3764 有效跌破=熊市确认 | break_policy / break_stop_service | 🧪 未启用 | 新代码，alert/execute_index 未开 |
| 84 | 2016-02-26 | 严格交易纪律；没到点位不追 | stop_loss_price | 📜 历史 | 早期口径，勿混读 |
| 85 | 2016-06-24 | 有效跌破前高/跳空→出场；否则持有 | stop_loss_price | 📜 历史 | 早期口径，勿混读 |
| 86 | 2016-08-05 | 跌破趋势线/大盘崩→才动 | stop_loss_price | 📜 历史 | 早期口径，勿混读 |

**读法**：✅ 15 条，⚠️ 37 条，❌ 14 条 + 🧪 17 条，📜 3 条。问题不在“缺规则”，而在**同一件事被多个模块用不同口径实现**，以及**点位表没有成为动作源**。

---

## 2. 必须先解决的 12 个冲突

| # | 冲突 | 账本 | 代码现状 | 后果 | 处置 |
|---|---|---|---|---|---|
| X1 | 个股级止损是否自动 | 2026-08-27「只看指数大级别…转下跌 1 浪就止损」；2026-08-25「顶多就是指数破位后的止损」 | `WOLF_STOP_BY_INDEX_BREAK=0`，六层个股止损照跑 | 与账本口径相反；audit 也显示个股止损负贡献 | 先降级为 alert；用户批准后再切 index-only |
| X2 | 盘中价当收盘价 | 2025-11-21「盘中跌破不算」×3；2026-01-29「收盘跌破我才出」 | P4 `_append_live_quote` 把 14:50 现价当当日 bar，`phase="close"` | 14:50 就触发“收盘破位”，提前卖 | P4 改 15:05 官方收盘；14:50 只出 `intraday_break` 预览 |
| X3 | 自设 B3 分层 | 账本只有点位→仓位，没有“1 个结构 vs 2 根 MA” | `break_policy` B3-1/B3-2 用 `deep_major_min=2` 和 `deep_level_types` | 不是他的规则；P5a 结论被污染 | 删除 B3-1/2 分层；改为“点位表 + 指数 MA/趋势兜底” |
| X4 | 90 日低点 −3% / HWM −10% | 账本没有这条 | `stop_loss_monitor._check_break_low_stop` | 自造百分比止损，可能卖在半路 | 标记 legacy，退出自动卖；只保留展示 |
| X5 | 最近支撑即时破 | 账本要求收盘/放量/缩量分支 | `t_monitor.custom_support_sell` 旧口径：现价 ≤ 最近支撑 | 盘中插针、轻微回落即卖 | 默认切 `WOLF_BREAK_SUPPORT_TREND=1`；旧口径只提示 |
| X6 | `seen_above` | 账本没有 | P2 要求“曾收盘站上”才算破位 | 会挡掉“从上方跳空跌破”的真破位 | P1 改成 `role=support/resistance`，P2 只处理 support；删除 `seen_above` |
| X7 | 缓冲/量比阈值 | 账本只说“放量/缩量” | 0.3% / 1.2 / 0.8 / 2.0 | 代理参数被当成规则 | 标 `source=proxy`；进配置；回测做敏感性 |
| X8 | B3 全部禁新开 | 账本只对 down-wave 明确“不做” | P3 所有 B3 `block_new_buy=True` | B3-1 也禁新开，过严 | 只有 down-wave 禁新开；B3-1/2 只降仓 |
| X9 | `reclaim=MA60` / `base_target=1.0` | 账本说“站回趋势线 3922”，没说 MA60 | P5a recover 臂用 MA60 + 100% | P5a 的 recover 结论不可用 | 生产不传；回测改成“站回被破 level” |
| X10 | 指数只有 MA60/144/200 | 账本点名 3922/3900/3888/3850/3731/3828/3761/4141/4100/4000/3344/3334/3370 | `t_index_break` 只算三条 MA；点位表在 `t_capacity` 但未接硬闸 | 账本的点位梯子没落地 | 点位表成为 `target_override` 的权威源 |
| X11 | 板块指数缺失 | 2026-05-06「以板块指数为准」 | 只有上证/个股 | 个股破位没有板块参照 | P2 增加 sector domain；先用 ETF/行业指数代理，标 proxy |
| X12 | P4 任务时点 | 收盘确认、时点门 | `break_stop_1450` + `--live` | 14:50 误判收盘 | 任务改 15:05；14:50 只预览、不落动作 |

---

## 3. 重新设计

### 3.1 架构：一个事实层 + 三条判据线 + 一个动作表 + 一个执行口

```
事实层（程序，只给事实，不下结论）
  index_bars / stock_bars / sector_bars / quote / volume / m15
  wave_state(level/operation) / positions / point_table
        │
        ▼
判据层（三条线，互相独立）
  R1 指数硬闸：level=down / 点位表 / MA60-144-200
  R2 趋势线：13/34/60、5日线（复用 wolf_trend_stop）
  R3 个股逻辑：13日/−3%、被动止盈（复用 wolf_early_stop / wolf_passive_stop）
        │
        ▼
动作层（只认账本动作，不造 B3 分层）
  hold / reduce_t / reduce_half / reduce_to(pct) / clear / no_new
        │
        ▼
执行层（唯一口）
  t_monitor → t_gateway（T+1、底仓、时点门、幂等）
        │
        ▼
账本层
  break_events / break_actions + provenance 标签
```

**不再新增“第六条止损规则”**：新代码只做**编排 + 点位表 + 账本**；真正的线复用已有的 `wolf_trend_stop`、`wolf_early_stop`、`wolf_passive_stop`、`t_stop_loss_guard`。

### 3.2 三条判据线

| 线 | 触发 | 动作 | 账本 |
|---|---|---|---|
| **R1 指数硬闸** | `wave.level=down`；或上证收盘 < 点位表关键位；或收盘破 MA60/144/200（≥2 根） | `no_new`；`reduce_to(point_table)`；无点位时 down→0-30%、MA→50/30 | D 组、H 组 |
| **R2 趋势线** | 收盘 < MA60 / MA34 带量 / 黑K破 5 日线 | exit / reduce | C 组 |
| **R3 个股逻辑** | 建仓 13 日内破波段低点 −3% 未收回；被动止盈线破 | 默认 alert；用户开启后才 reduce | B 组、F 组 |

**优先级**：R1 > R2 > R3。R1 触发时，R3 不再逐只清仓；按 R1 的目标仓位卖弱留强。

### 3.3 动作表（替代 B3-1/2/3）

| 场景 | 动作 | 目标 |
|---|---|---|
| `wave.level=down` | `no_new` | 0；有短线仓时 ≤30% |
| 上证收盘 < 点位表（如 3922/3900/3888/3850） | `reduce_to(point)` | 点位表值（40/20/50/35） |
| 上证收盘 < MA60/144/200（无点位） | `reduce_to(50/30)` | 50% / 30% |
| 个股/板块破位 | `alert`（默认） | 不动；用户开启后 `reduce_half` |
| 13 日 / −3% 未收回 | `alert`（默认） | 不动；用户开启后 `reduce_half` |
| 被动止盈线破 | `reduce_t`（已有） | 只减 T 仓 |

**点位表**：`t_capacity.level_target_pct()` 已有；把它作为 R1 的**权威目标**，不再用 P3 的 50/30 去覆盖它。C1 3922→40、C3 3850→35 都按表走。

### 3.4 事实层要补的四个字段

1. **level.role = support / resistance**：P2 只处理 support，彻底删除 `seen_above`。
2. **level.point_type**：`platform / trendline / round / prior_day_low / prior_day_high / ma / gap`，与账本术语一致。
3. **point_table**：`close → target_pct`，来源 `t_capacity.WOLF_TARGET_TABLE`；没有表就不猜。
4. **data_quality**：`ohlc_complete / bars / asof / source`；缺数据不卖。

### 3.5 时间语义（必须改）

| 时点 | 模式 | 行为 |
|---|---|---|
| 14:50 | `preview` | 只用 `phase=intraday`，只记录 `intraday_break`，**不写动作、不推“已破位”** |
| 15:05 | `official` | 用当日收盘 bar，按 `phase=close` 写事件/动作，推 QQ |
| 盘后回放 | `replay` | 只用 `asof` 及以前；禁止次日 reclaim 参与当日动作 |

**不要**在 14:50 用现价冒充收盘。P4 的 `--live` 只能用于 preview，不能写 `break_actions`。

### 3.6 执行与去重

- `t_monitor` 是唯一卖出口；`stop_loss_monitor` 只做展示；不再新增卖出逻辑。
- 幂等键 `(asof, symbol, action)`；同一票同日同动作只执行一次。
- 时点门 `_stop_time_ok()`、T+1、底仓 floor、跌停/停牌，全部复用。
- 卖出顺序：R1 降仓时“弱 + 流动性好”优先；不按持仓顺序无差别卖。

### 3.7 来源标签（防止再自设）

每个 level/rule/param 带 `source`：
- `corpus`：有日期+原话（A 类）
- `proxy`：语料只给方向，数值/分层是我定的（B 类）
- `engineering`：无原话，只为可计算/防误报（C 类）
- `backtest`：只用于回测（D 类）

**测试要求**：新增参数如果没有 `source`，测试失败；`source=proxy/engineering` 的规则不得写成“他的规则”。

---

## 4. 代码处置清单

| 文件/模块 | 处置 | 具体 |
|---|---|---|
| `wolf_trend_stop.py` | **保留** | R2 主体；13/34/60、5 日线 reduce 已对齐 |
| `wolf_early_stop.py` | **保留，默认 alert** | R3；13 日/−3% 有账本，但 audit 负贡献；自动执行需用户批准 |
| `wolf_passive_stop.py` | **保留** | 被动止盈线；只减 T |
| `t_stop_loss_guard.py` | **保留** | 收盘确认/收回/量能守卫 |
| `t_index_break.py` | **扩展** | 增加点位表/趋势线/平台；不再只 MA |
| `support_resistance.py` | **扩展** | 输出 `role`/`point_type`；点位表 |
| `break_levels.py` | **改** | 加 `role`；删除 `seen_above` 依赖；点位表 |
| `break_detector.py` | **改** | 只处理 support；`intraday` 不确认；`close` 才确认 |
| `break_policy.py` | **删 B3-1/2/3 分层** | 改为点位表 + down-wave + 三条线；`block_new_buy` 只 down-wave |
| `break_stop_service.py` | **改** | 15:05 official；14:50 preview；不再 live→close |
| `stop_loss_monitor.py` | **降级** | `_check_break_low_stop` 标 legacy；只展示 |
| `t_monitor.py` | **唯一执行口** | 旧 `custom_support_sell` 默认切 trend 口径；新增 `break_stop` 读动作 |
| `t_capacity.py` | **接线** | 点位表驱动 floor/target；`target_override` 权威 |
| `config/tasks.yaml` | **改** | `break_stop_1450` → `break_stop_1505`；14:50 只 preview |
| `daily_decision.py` L6 | **接线** | 把点位表/破位状态写进决策对象 |

**明确不做**：
- 不再新增 B3-1/2/3 这类自设分层。
- 不把 `seen_above`、0.3% 缓冲、1.2/0.8 量比写成“他的规则”。
- 不在 14:50 用现价当收盘。
- 不在个股层自动清仓（默认 alert）。
- 不在 `stop_loss_monitor` 新增卖出。

---

## 5. 实施计划（v2）

| 阶段 | 内容 | 工期 | 验收 |
|---|---|---|---|
| **P0 矩阵/冲突** | 本文 + 86 条矩阵 + 12 冲突 | 0.5d | 每条账本有判定/代码/处置 |
| **P1 事实层** | `role=support/resistance`、`point_type`、点位表、`data_quality`；删 `seen_above` 依赖 | 2d | 86 条中 A/B/C 类所需 level 都能算；无未来数据 |
| **P2 三条判据线** | R1/R2/R3 独立；R1 只认 index/down/点位/MA；删除 B3 分层 | 2d | C1/C3/C10 动作按点位表；个股默认 alert |
| **P3 动作层** | `reduce_to(point)`、`no_new`、`alert`；点位表权威 | 1.5d | C1 3922→40、C3 3850→35、C10 down→0-30 |
| **P4 接线** | 15:05 official；14:50 preview；t_monitor 唯一执行；时点门 | 2d | 14:50 0 动作；15:05 事件/动作正确；0 重复卖 |
| **P5 回测** | 指数点位表 vs MA60 vs down-wave；**不用 MA60 reclaim**；A/B 两版 | 2d | 左尾/收益/换手；结论带口径 |
| **P6 启用** | 先 alert；用户批准后只开 R1；R3 保持 alert | 1d | 生产无重复卖；可一键回退 |
| **P7 Agent** | 可选；只评估性质，不造价格 | 1.5d | 10 判例盲测 |

**P5 回测必做三件事**：
1. 用 `role=support` 后的 level，不再用 `seen_above`；
2. 恢复线改成“站回被破 level”，不用 MA60；
3. `base_target` 只在回测里，生产不传。

---

## 6. 验收

### 6.1 账本覆盖
- 86 条矩阵每一行都有：判定、代码/模块、处置。
- A 类（原话直接）每条至少有一个代码落点或明确“不做”。
- B/C 类每条带 `source=proxy/engineering`，不得写成原话。

### 6.2 判例
- C1 2025-11-21：3922→40%、3900→20%；盘中不算。
- C3 2025-12-03：3888→50%、3850→35%、3816→50%、3922-3930→70%。
- C10 2026-10-15：3764 有效跌破 → down-wave，0-30% cap。
- C12 2025-12-11：前低跌破 + 3×15min 不收回 → 反抽减仓。
- 2026-03-23：13:00-14:30 只预警不执行。
- 2026-08-27：level=down → 止损/不做。

### 6.3 工程
- 14:50 不写 `break_actions`；15:05 才写。
- `(asof,symbol,action)` 幂等；重复跑不重复卖。
- `WOLF_BREAK_STOP=0` → 生产行为不变。
- 所有 proxy/engineering 参数有 `source` 标签。

---

## 7. 需要你拍板的 6 件事

1. **个股级止损**：是否按账本降级为 alert（`WOLF_STOP_BY_INDEX_BREAK=1`）？还是先保留六层？
2. **点位表**：是否把 3922→40、3900→20、3888→50、3850→35、3816→50、3922-3930→70 写进 `WOLF_TARGET_TABLE`，作为 R1 权威目标？
3. **down-wave**：默认 0，还是 30% cap？有短线仓才 30% 是否保留？
4. **`seen_above`**：按 P1 的 `role` 重构后删除，还是保留为工程开关？
5. **`wolf_early_stop`**：账本说“直接止损”，audit 说负贡献；默认自动、alert，还是按品种分？
6. **P4 时点**：任务改 15:05，14:50 只 preview —— 确认？

---

## 8. v3 实施记录（2026-10-23）

用户拍板后，已按“A + 旧规则直接废弃 + 保留执行管线”重写：

| 决策 | 落地 |
|---|---|
| 旧规则模块 | 新层 **不 import** `wolf_trend_stop` / `wolf_early_stop` / `wolf_passive_stop` / `t_stop_loss_guard` / `t_index_break`；旧模块待切换后删除 |
| 执行管线 | 保留 `t_monitor` / `t_gateway` / `t_capacity`；新层只返回 `StopDecision`，不下单 |
| 个股级 | **alert only**；`WOLF_STOP_BY_INDEX_BREAK=1` |
| down-wave | **一律 30% cap**，`no_new=True` |
| 指数计算级破位 | 用 `PointEngine` 算关键位；收盘破位 → 目标 = `tier_target_pct(operation)`（2026-01-17 分档），**不硬编码点位** |
| 点位表 | **不做**；点位由引擎计算 |
| `seen_above` | 删除；改为 `role=support/resistance` + 用上一交易日算点位 |
| `wolf_early_stop` | 暂不纳入；后面单独探究 |
| `wolf_passive_stop` | 随旧模块删除 |

**新增文件**：
- `backend/app/services/point_engine.py`：缺口/趋势线/BOLL/颈线/前高低/对称/测量/0.382-0.618/成本区/整数关/波浪锚点；输出 `type/price/source/confidence/role`。
- `backend/app/services/wolf_stop_layer.py`：`StopContext`/`StopDecision` + `evaluate()`；down-wave 30、计算位破位→tier target、个股 alert、guard。
- `backend/tests/test_wolf_stop_layer.py`：12 tests；含他历史点位的外部校验（3922/3931、3373、3334、4000/3888、3674/3731、4100/4141 等，±0.6%）。

**当前测试**：P1-P4 + v3 + t_monitor 接线 = **75 passed**。

**已接线（2026-10-23）**：
- `t_monitor` 的 `_check_stop_loss` 已**恒走** `wolf_stop_layer.evaluate()`（旧路径删除，`WOLF_STOP_V3` 仅留常量兼容）；
- 旧止损/减仓腿整段旁路：`_legacy_stop_cycle()` 已删除；条件腿 `stop_loss` / `custom_support_sell` / `wolf_passive_stop_sell` / `wolf_early_swing_sell` / `wolf_boll_mid_exit` 直接跳过；
- v3 的 `reduce_to` **只写 `data/stop_v3_events.jsonl` + print，不自动卖**；
- T/兑现类腿（`high_sell`、`custom_vwap_sell`、`board_half`、`profit_take`、`weekend_hedge`、`fib_target`、`boll_sell`）保留。

**已删除（2026-10-23，用户“旧模块直接删除”）**：
- `wolf_trend_stop.py`、`wolf_passive_stop.py`、`t_stop_loss_guard.py`、`t_index_break.py`、`stop_loss_monitor.py`、`backtest_stop_loss.py`；
- 旧测试/任务：`test_wolf_trend_stop.py`、`test_t_stop_loss_guard.py`、`test_index_breakdown.py`、`test_p2_4_stop_close_confirm.py`、`test_wolf_exit_targets.py`、`jobs/eval_stop_layers.py`；
- `t_monitor` 的旧 `_check_stop_loss` 主体、`_check_passive_stop`、`_check_index_level_stop`、`_legacy_stop_cycle`、`_stop_close_confirm` 已删；
- `stop_loss_monitor` 的启动/状态/命令已从 `worker_main`、`scheduler_service`、`main.py` 移除；`/api/v1/scheduler/stop-loss-monitor/start|stop` 现在返回“已删除，v3 由 t_monitor 接管”；
- `t_index_break` 的指数 MA 破位逻辑迁入 `point_engine.index_breakdown/index_close/index_mas`；`daily_decision`、`leg_gate`、`break_stop_service`、`wolf_context` 已改读；
- `t_backtest` 的假跌破守卫改为 `wolf_stop_layer.guard_stop`。

**保留（后续单独探究）**：`wolf_early_stop.py`（用户明确“后面再仔细探究”）。

**下一步**：
1. v3 灰度 2-3 天，对比 `data/stop_v3_events.jsonl` 与旧日志；
2. 14:50 只 preview、15:05 official；不再 `--live` 当收盘；
3. 用真实历史 `wave_state` 回测 down-wave（当前只有 MA60 代理，见 §9）。

## 9. v3 第一版回测（指数级）

报告：[wolf-stop-v3-backtest.md](wolf-stop-v3-backtest.md)
逐条对照：[v3-break-vs-ledger.md](v3-break-vs-ledger.md)

**先说结论**：`v3_break` 的 32 次止损里 **0 次完全符合账本**；约 8 次方向/位置相近但目标或方法不符；其余账本没有对应条件。**它不是狼大的规则**，不能拿来证明/否证账本。

**方法论错误已确认**：这轮又把“关键位”当通用筛选器、把目标自设成 50%，违反 `ai-agent-lessons-from-b-rebound.md` §2.2/§2.3/§9.2；`v3_break` 的回测数字对狼大规则**作废**，下一步改成账本事件研究。

**D 组判例对照**：[ledger-stop-cases-vs-v3.md](ledger-stop-cases-vs-v3.md)：17 条里 7 条未触发；8 条他本人触发动作中 **0 条完全符合**（5 漏 / 1 反 / 2 部分）；另有 1 条误触发（D02）、1 条 D17 是描述量化（不计入）。

**账本事件研究**：[ledger-event-study.md](ledger-event-study.md)：只取 4 条明确减仓动作（D04/D10/D11/D12），5/10/20 日**0/4 跑赢 hold**（20 日平均超额 -1.84pp），但 **4/4 回撤更小**（平均改善约 +1.38pp）。样本 n=4、事件重叠，不能当统计结论。

| 臂 | 总收益 | 最大回撤 | Sharpe | 平均仓位 | 换手 |
|---|---:|---:|---:|---:|---:|
| hold | +28.53% | -11.28% | 1.46 | 100% | 0 |
| down30（MA60 代理） | +10.15% | -12.65% | 0.78 | 81% | 16 |
| v3_break（计算位→50%） | +17.87% | -8.98% | 1.29 | 87% | 40 |
| v3_break_down | +8.43% | -12.03% | 0.82 | 77% | 49 |

**读法**：
- 计算位破位→50% 把最大回撤从 -11.28% 压到 -8.98%，但收益少 10.7pp；方向对，代价大。
- down30 的 MA60 代理**滞后**：跌破 MA60 才减、站回才加，结果回撤反而更大；**不能用它证明“down-wave 30% 无效”**。
- 真实检验需要历史 `wave_state`（`data/` 只有 34 个快照），当前结论只能作为机制方向性对照，不能当成“狼大规则已验证”。

---

## 附录：本次对比的方法

1. 账本 86 条按 A-I 分组（`docs/wolf-stop-loss-ledger.md`）。
2. 每条按“日期 + 判据/动作”对到代码模块；代码位置用模块名而非行号，避免行号漂移。
3. 判定码只看“账本语义是否在代码里成立”，不看“代码是否赚钱”；收益有效性另见 `stop-layer-audit.md` / `break-stop-backtest.md`。
4. 任何“代码有、账本无”的规则，一律标为代理/工程，不进入 A 类。
