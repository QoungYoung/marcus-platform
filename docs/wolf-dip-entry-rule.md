# Wolf 主线内低吸执行规则（2026-09-09 落地）

> 来源: 狼大语料(2020-2026 sheets) 原话核验; 对齐代码: mainline_confirm_state.py / mainline_gate.py / rotation_switch_arm.py(准入)

## 一、资格闸：只有「主线已确定」的回调才低吸

Wolf 原话:
- 2025-02: "已经确定了主线的主升浪…低下去也要买, 涨起来也要追" —— 低吸前提=主线已确定
- 2026-01: "再3-3还是确认的情况下…把握每一次的低吸机会" —— 确认后每次回调低吸是节奏
- 2026-08-04(反例/材料): "目前走大2浪 不参与 坐等企稳和指数共振走主升大3, 我才不去硬抗这个大2的ABC"
  —— 结构未确认方向的大 2 浪 **不接**; "reserve"≠入场许可

代码(实际, 2026-09-10 核对): `rotation_switch_arm.gate_confirmed_today()` —— 取**最近一份** `mainline_gate_*.json`
  (日期 ≤ today) 的 `confirmed_candidate` 主题集, 即"最新 gate 口径"; **并非** 40 交易日"曾确认"窗。
  注: 上句原写的"LOOKBACK=40 交易日内 = 有资格"是**设计意图, 尚未接入**(见文末"接入点"更正)。
数据: `mainline_confirm_history.json` 由 `mainline_gate` 每日 `ensure_history` 追加(`mainline_confirm_state` 维护);
  但其 40 日窗消费函数 `chain_qualified` / `ts_qualified` 目前**无任何调用点**(仅有定义), 属"设计待接入"。

## 二、回调判定：不破前低 + 地量缩量，非结构 PASS 当天

Wolf 原话:
- 2026-02-02: "地量后…大盘没过前低是前提, 观察板块/个股也没低于前低是基础条件"
- 2025-10: "3-2结束转3-3是那个跳空向上大红K…大红K前探底挖坑/横盘挖坑更具欺骗性"

执行: 254(dip_prev_low) 需 收盘不破前低 + 缩量/地量(confirm_chain S1 量能口径);
253(m5dump) 仅限有资格主题内的回调首确认。挖坑段(骗线)不加, 等放量。

## 三、加仓：放量收盘破前高(3-3 大红K)

Wolf 原话: 2025-10-31 "转3-3要站稳3天"; 2025-08-13 "3-2结束转3-3是跳空向上的大红K"
执行: 收盘放量破前高(趋势再确认) 才加仓; 不追单日冲高。

## 四、破位：收盘破=客观事实, 出清不补

Wolf 原话: 2026-01-29 "今天没跌破我没出, 我说了 收盘跌破我才出"; 2026-01-12 "等收盘确认破位出清"
执行: 已持有主题收盘破前低/2浪低 → 按 auto_exit/破位腿走, 禁止补仓(与 ③ 不冲突: 破位=资格注销)

## 接入点(v1, 2026-09-09; 2026-09-10 更正)

> **更正说明**: 本文原写"is_main 判定升级为 mainline_confirm_state.chain_qualified", 与代码不符。
> 2026-09-10 核对结论: `chain_qualified` / `ts_qualified`(`mainline_confirm_state.py:70/:98`) **已定义但无任何调用点**;
> `rotation_switch_arm.py` 买侧实际调用的是 `gate_confirmed_today(today)`(同文件 `:214`)。

- rotation_switch_arm.py 买侧(`:296` 起): `qualify` 开启时用 **`gate_confirmed_today`** —— 只放行"最近一份 gate"里
  的 `confirmed_candidate` 主题; 未在其中的主题不布新建腿(`SKIP_MAINLINE_LOWBUY not_today_confirmed:*`)。
  代码注释亦自述"曾确认但结构回落(watch)不布新建腿" → **实际口径比"40 日曾确认窗"更严**。
- env `MAINLINE_QUALIFY=0` 可回退旧 main_line_state 逻辑; `SWITCH_ARM_DRY=1` 观察。
- **待接入(设计已写、代码未接)**: 40 交易日"曾确认"资格窗(`chain_qualified`/`ts_qualified`);
  若接入, 语义会比现在**更宽**(允许结构回落到 watch 的主题仍在窗内低吸), 需与 P1-1/P2-2 一并决策。
- 农业案例验证: 8-12 首次 confirmed(资金rank1+结构PASS) -> 8-21/24 回调低吸资格在窗内(成立 +13.2%)
- 待 step2 完整: 资格中的"资金未跑"以曾 confirmed 代替; ETF份额/两融接入后加第二道闸
- 另注(2026-09-10 核对): 本文依据的两处机制当前状态已变——
  (a) **主题 ETF 兜底腿**(空窗时兜底买 ETF)已改为**默认关闭**(与狼大"买不到位置就等"相反);
  (b) **风向标存活判据 `wind_broken` 目前恒为假**(其判据 `close/same_day_low-1` 恒 ≥0, 数学上不可能 ≤−0.5),
      故"风向标死了就不做"**实际未生效**; 且该硬拦还需 `WOLF_PICK_WIND_HARD=1`(默认 0)才启用 → 修判据时须同时定该开关默认值。
