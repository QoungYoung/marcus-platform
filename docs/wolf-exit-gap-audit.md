# 退出/兑现层落差审计（我们的系统 × 狼大的兑现策略）

> 输入：docs/wolf-exit-evidence.md（1,105 条 dsh 逐条精读，99.6% 逐字可核）+ docs/wolf-exit-playbook.md
> + **生产实测**（2026-09-14，容器内运行时探针 + PG `wolf_discipline_config` + `t_triggers` 近 25 天统计）。
> 状态口径：✅ 已落（机制在跑且与他的话一致）｜⚠️ 落不完全（有机制但缺前提/缺分支/只到提示层/实际空转）｜⛔ 没落。
> **本文件只做审计，不改生产**；标"建议动作"的部分需要用户拍板才动。

---

## 0 一张总表

| 他的话（类） | 我们对应机制 | 生产实测状态 | 判定 |
|---|---|---|---|
| E1 做 T 兑现：3–5 个点（下沿 3） | `roundtrip_sell`（低吸均价×1.03 卖回旧仓） | 开，幅度 0.03 | ✅ |
| E1 尾盘必须出场、不留隔夜 | `wolf_day_end_de_t`（14:45 后当日正T未T出→减T仓） | 在跑 | ✅ |
| E1/E3 **小赚兑现**（浮盈≥3% → 减 T 半仓） | `profit_take`（DB 配置） | **2026-09-14 开启**（enabled=true，覆盖 stock+t） | ✅ |
| E1 底仓不动 | `base_floor_shares`（floor=累计买入×0.5，下限100股） | 在跑，2026-09-14 加穿透白名单（中轨全止盈可清底仓） | ✅ |
| E1 三日未达预期=认错 | — | — | ⛔ |
| E1 出上影线→停止做 T | — | — | ⛔ |
| E2 破分时黄线直接走 | `quote.vwap_break` → `custom_vwap_sell`（stock 持仓自动布腿）+ roundtrip_sell 内黄线优先 | 在跑，**近 25 天 173 条被拦、成功 1 条** | ⚠️ 形式落地、实际空转 |
| E2 二次冲高缩量不过前高就走 | `minute.m5.t_sell` → `high_sell` 腿 | 在跑，**214 条被拦、成功 6 条** | ⚠️ 同上 |
| E2 破位走（跌破最近支撑） | `quote.break_support` → `custom_support_sell` | 在跑，**173 条被拦、成功 1 条** | ⚠️ 同上 |
| E2 破前低→"删票"（移出观察池） | — | — | ⛔ |
| E2 13 天周期 + 放量长上影→撤退 | `logic_time_stop`（13 日内未碰新高→离场） | 默认开 | ⚠️ 只覆盖"没碰新高"，不含"放量长上影" |
| E3 吃一口减一半 / 板上减半 | `board_half`（今涨≥9.5%/20%板≥19.5% 且浮盈≥3% → 减半） | DB 配置 enabled=true | ✅ |
| E3 板块高潮/冲高大减 | `wolf_defensive_t_reduce`（wave只做T ∧ 量能不足或滞涨） | 在跑（近 25 天成功 2 笔） | ⚠️ 判据不同 |
| E3 高位卖强留弱 / 低位留强丢弱 | 只有统一"去弱留强"（持仓层提示） | 提示层 | ⛔ |
| E3 到关键位/压力位分档减（144 线、0.618、双头） | BOLL 上轨减半（`WOLF_BOLL_SELL` 默认 1）作代理 | 在跑 | ⚠️ 口径不同 |
| E3 一个票最多 2 买 2 卖 | — （只有 `WOLF_REFILL_MAX_PER_DAY=2`=单日回补上限） | — | ⛔ |
| E3 大涨日多卖、大跌日多买 | — | — | ⛔ |
| E4 顶部阶段：中轨全止盈 | `wolf_boll_levels.mid_break_sells` + `WOLF_BOLL_MID_EXIT=1` | **2026-09-14 改直连执行 + 底仓穿透**；但"顶部阶段"前提实测仍不满足（pos=0.259 < 0.8）→ 休眠（G1 待做） | ✅执行层 / ⚠️触发率 |
| E4 上轨减半锁利 | `WOLF_BOLL_SELL`（默认 1） | 开 | ✅ |
| E4 被动止盈位只上移、不破不卖 | `stop_loss_price`（静态）+ `wolf_early_stop` 结构线 | 在跑 | ⚠️ 无"只上移"的移动止盈（`trail_break` 已作为自造机制删除） |
| E4 浮盈>100% → 用 13 日线/中轨保护 | — | — | ⛔ |
| E4 个股止盈点 = 前波幅度 0.618 | — | — | ⛔（他有明确口径，N1） |
| E5 周末 2:30 缩量∧未拉升 → 出一半、65% 过周末 | `wolf_weekend_hedge`（前提齐） / `weekend_de_risk`（只看周五+仓位≥50%） | **2026-09-14 进执行层**（T 仓减半、底仓不动、stock+t） | ✅ |
| E5 周一拿回 / 避险结束要补回来 | — | — | ⛔ |
| E5 节假日"早盘卖、尾盘买"的反 T | — | — | ⛔ |
| E6 指数大级别（转下跌 1 浪）止损 | `WOLF_INDEX_LEVEL_STOP` + `wolf_index_context` | 默认开 | ✅ |
| E6 个股建仓初期逻辑止损（13 日/−3%/无利空） | `wolf_early_stop`（`early_stop_price` + `logic_time_stop` + `negative_event`） | 默认开 | ✅ |
| E6 收盘确认破位才出清 | `_stop_close_confirm` + `_in_close_window`(≥14:55) + `_stop_exit_volume`(收盘清仓含底仓) | 默认开（`WOLF_BASE_EXIT_CLOSE` 默认 1） | ✅ |
| E6 不在杀跌里割 / 地量不割 | 量能分层 + `WOLF_SLOW_DECLINE`（缓跌） | 默认开 | ✅ |
| E6 止损时点：13:00–14:30 不做 | `_stop_time_ok` | 默认开 | ✅ |
| E7 尾段：不加仓、顶多做 T、最后一段撤 | — | — | ⛔（我们判定条件不可识别 → 不做，防前视） |
| E7 放量上影线 ≥2×10 日均量 = 见顶 | — （"顶部阶段"只用上证 120 日分位） | — | ⛔（N3） |

---

## 1 三处结构性冲突（**2026-09-14 已按用户拍板处理**：C3 开启 / C1 按他的策略开穿透 / C2 进执行层；状态见 §5）

### C1 底仓保护 vs 他"该动底仓时会动" → 兑现腿大面积空转 　✅ 已处理（只放行中轨全止盈穿透）
- **我们**：常规兑现腿的卖出量 = `min(可卖, 净持仓 − floor)`，`floor = 累计未 void 买入量 × T_BASE_KEEP_RATIO(0.5)`，下限 100 股
  （`t_gateway.base_floor_shares`）→ **最多只能卖到半仓**。
  例外：止损/破位走 `_stop_exit_volume`，**收盘窗口(≥14:55)可清仓含底仓**（`WOLF_BASE_EXIT_CLOSE` 默认 1）。
- **实测后果（近 25 天 t_triggers）**：卖腿 **blocked 602 条**理由为「量推导为 0（仅底仓无T仓可卖）」，
  另有 41 条「无可卖底仓」；同期成功执行：high_sell 6 / custom_vwap_sell 1 / custom_support_sell 1 /
  custom_trail_sell 1 / high_sell_then_buy_back 15 / stop_loss 1 / wolf_defensive_t_reduce 2。
  → **"破黄线""二次冲高缩量""破支撑"三条腿名义在跑，实际几乎不成交。**
- **他**：底仓在正常持有期确实不动（2025-05-27「我T了一把 底仓不动」），但**这些场合他会动底仓**：
  顶部阶段中轨全止盈（2025-05-13「全止盈的位置就放在日线 BOLL 中轨附近，放量跌破收盘完全止盈」）、
  高位方向"卖强留弱、拉升后都走"（2026-09-04 15:07）、节假日/周末避险（2026-08-21、2025-04-29「我降仓位到 10%」）、
  关键位分档减（2025-04-09「3221 如果任何时候到这个点，我会想办法卖到 40% 仓位以下」）。
- **建议动作**：给"顶部阶段全止盈 / 高位方向拉升后 / 周末避险"三类**各自带开关的 floor 穿透**（默认关、先影子），
  否则这三条规则是"形式落地"。⚠️ 需用户拍板。

### C2 避险只有"减"，没有"拿回"，而且只到提示层 　✅ 执行层已落（回补腿待设计）
- **我们**：`weekend_de_risk`（只看周五 + 仓位≥50%，**缺"缩量∧未拉升"前提**）走提示/agent 通道；
  `wolf_weekend_hedge`（前提齐：14:30、缩量、未拉升、65% 目标）**只写提示文本，不自动执行**。
- **他**：2026-08-21 14:20 预告 → 14:35「**2点半过了 我按刚才说的操作了**」（**当场执行**），
  并明确「**这样周一再拿回来**」；2025-09-24 更进一步：「你出于什么原因出去避险 **那这个避险逻辑结束后 是不是应该补回来** 在个股逻辑没变的情况下」。
- **建议动作**：① `wolf_weekend_hedge` 加"影子模式"（记录 would-sell 量，不下单）→ 样本够了再升为执行；
  ② 新增**回补腿**（他现在没有；只减不加会系统性漂成低仓）。⚠️ 需用户拍板。

### C3 "兑现幅度"只覆盖当日低吸，存量老仓没有任何幅度止盈 　✅ 已开启 profit_take
- **我们**：`roundtrip_sell`（+3%，`WOLF_ROUNDTRIP_SELL_UP=0.03`）只对**当日低吸买入**的标的生效（`pending_symbols`）；
  通用"浮盈≥3% → 减半"的 `profit_take` **开关为 false**（DB `wolf_discipline_config.profit_take.enabled=false`，
  当初不开的理由是怕与 board_half/defensive/roundtrip 叠加）。
- **他**：「**正常收益就是 3-5 个点**」（2026-04-23）；「**T+0 2 个点我就够了**」（2025-04-03）；
  做反抽「**我的目标就是 5 个点** 目前药已经达到了 我就撤了 **卖飞总比亏损好**」（2025-04-01）。
- **建议动作**：这就是 plan §5 阶段 1 的 **V1 变体（+3% 目标幅度）**；但**必须先量化叠加效应**（board_half/defensive/roundtrip 三者同标的同日冲突），
  并用**新尺子**（stock 账户 + 回合级）验收。⚠️ 需用户拍板开关。

---

## 2 其余差距（按"是否值得做"排序）

| # | 差距 | 依据 | 建议 |
|---|---|---|---|
| G0 | **死腿仍在跨日结转**：`custom_trail_sell`（quote.trail_break 恒 False，机制 2026-09-10 已删）每天被 `_roll_wolf_legs` 复制成新条件 | 实测 | 结转时按 kind 白名单过滤（`custom_vwap_sell`/`high_sell`/`custom_support_sell` + 狼大直出腿），把死腿清出 |
| G1 | **顶部判据太窄**：只有上证 120 日分位（实测 pos=0.259 → 中轨全止盈当前休眠）；他还有"放量上影线 ≥2×10 日均量"（2025-05-06）、"银保长上影+放量+大盘缩量"（2025-05-15）、"顶部现象"（2025-02-21） | E7 | 可算化两条量价判据 → 作为**并列前提**（不是替代），先离线验收 |
| G2 | **个股止盈点 = 前波幅度 0.618**（2025-04-23）没有 | E4/N1 | 可算化成本低（用建仓日锚的前一波拉升幅度），进阶段 1 变体池 |
| G3 | **破线后"删票"**（2025-02-06「最下面那根线一旦破了 卖出然后删票」；2025-04-03「破之前新低的，直接删票」）没有 | E2/N4 | 我们只卖不清池 → 会反复买回坏票；建议做**观察池剔除**（带 TTL，别永久拉黑） |
| G4 | **移动止盈缺失**：`trail_break` 已作为自造机制删除；他的"被动止盈位只上移"（2025-06-09「提高到 3373，不破不卖」）没有对应实现 | E4 | 与"删除自造机制"不冲突：他**有**原话 → 可以按"只上移"重做（默认关 + 影子） |
| G5 | **高位/低位两套相反规则**（2026-09-04：高位卖强留弱、低位留强丢弱）没有分型 | E3 | ✅ **已落地（2026-09-14）**：方向高低位口径 = 既有概念分类器 + 相对全局基准富集度（见 §5 G5 行）。仍缺的两个子条件：①「避开公募重仓」= 已删除的拥挤黑名单（用户 2026-09-13 自删）；②「辨识度」未可算化 |
| G6 | **一票最多 2 买 2 卖**（2025-02-07）没有 | E3/N5 | 属"腿数护栏"，与 `WOLF_REFILL_MAX_PER_DAY` 语义不同；实现简单但要防与回补链冲突 |
| G7 | **大涨日多卖、大跌日多买**（2025-01-23）没有 | E3/N7 | 卖出侧日内节奏规则；可先做分组统计看有没有效（我们已能算日内涨跌幅） |
| G8 | **出上影线 → 停止做 T**（2025-07-17）没有 | E1/N8 | 可算化（日线上影线比例/分时），当作"当日停机"开关 |
| G9 | **节假日反 T：早盘卖、尾盘买**（2025-04-29）没有 | E5/N6 | 与 C2 的回补腿同源，可作为回补腿的时点参数 |
| G10 | **尾段状态机** | E7 | 维持"不做"：条件不可识别（"一旦我认为行情结束"是主观念头），做了就是前视 |
| G11 | **诱空最多三次**（2026-09-07） | E2 | 需要分时/盘口序列语义，暂不可算化 → 不做 |

---

## 3 落得**对**的部分（不要动，避免"为改而改"）

- **止损层与他的口径高度一致**：只指数大级别 + 建仓初期逻辑（13 日/−3%/无利空）+ 收盘确认 + 时点门 + 地量不割。
  其中"收盘窗口才清仓、盘中只减半"（`_stop_exit_volume`）正是他"收盘跌破我才出"的实现，**不要改成盘中全清**。
- **"底仓不动"本身是对的**（他有 2025-05-27 原话）；问题只在"该动底仓的场景"没有开口（C1），不是 floor 本身错。
- **"吃一口减一半"**（board_half）与他的话一致；**注意它要求"今日触及涨停"**，而他的话在券商那次也是"板上减"（2026-09-01）→ 口径吻合。
- **做 T 时间窗（WOLF_TRADE_WINDOW=0）关着**是对的：此前回测两种口径都更差（保留关闭）。

---

## 4 生产实测开关快照（2026-09-14，供复核）

    .env：WOLF_BOLL_MID_EXIT=1  WOLF_CUSHION_CAP=1  WOLF_WEEKEND_HEDGE=1  WOLF_VOLUME_GATE=1
          WOLF_TRADE_WINDOW=0  WOLF_GAP_CAUTION=0  T_STOP_GUARD_ENABLED=0  WOLF_DECISION_GATE=1
          （WOLF_BOLL_SELL / WOLF_BASE_EXIT_CLOSE / WOLF_CLOSE_BREAK_HM / T_BASE_KEEP_RATIO 未显式设置 → 走代码默认 1 / 1 / 14:55 / 0.5）

    DB wolf_discipline_config（_source=db）：
      board_half      enabled=true   min_float_pct=3  板阈值 10%板 9.5 / 20%板 19.5
      profit_take     enabled=false  min_float_pct=3.0  reduce_ratio=0.5      ← 唯一"关着的兑现规则"
      weekend_de_risk enabled=true   windows=[late_morning, afternoon, closing]  th_ratio=0.5  reduce_to=0.5
      position_cap    enabled=true   tier_targets={build:75, t_only:50, side:50, defense:30, exit:50}

    运行时探针：roundtrip_sell 已加载（up=0.03）；wolf_boll sell_enabled=true / mid_exit_enabled=true，
      market_top={pos:0.259, thr:0.8, top:false}（**当前不满足"顶部阶段"**）；
      weekend_hedge enabled=true cutoff=14:30；early_stop enabled=true（各项 env 未覆盖 → 代码默认 1）

    卖腿生成：t_monitor._arm_stock_exit_legs 对 stock 持仓自动布 3 条持续腿
      （custom_vwap_sell / high_sell / custom_support_sell）
    ⚠️ 死腿：custom_trail_sell **停止新增但仍被跨日结转**（t_monitor._roll_wolf_legs 会复制"最近一日"的
      全部 wolf 条件）→ 2026-09-13 仍有 3 条 active（SH512480/SH588170 publisher=auto_exit、SH603259 manual_guard），
      而 quote.trail_break 恒 False（自造机制 2026-09-10 已删）→ **永不触发**，却会在"该标的有没有卖腿"的展示里
      冒充保护、并继续占条件表。建议：结转时跳过已删除机制的 kind（一行判断）。

---

## 5 兑现/出场待办清单（2026-09-14 更新：用户拍板后 C3/C1/C2/G0 已落地）

> 状态口径：✅ 已落地并在服务器生效 ｜ 🚧 进行中/待验收 ｜ ⏳ 待办 ｜ ⛔ 不做（前视/数据不足）。
> 落地提交：`62c2284`（代码+单测）、DB 配置改动在服务器 `wolf_discipline_config`。

| # | 事项 | 他的依据 | 状态 | 落点 / 备注 |
|---|---|---|---|---|
| **C3** | 小赚兑现 profit_take（浮盈≥3% → 减 T 半仓、保留底仓） | 「正常收益就是 3-5 个点」2026-04-23；「T+0 2 个点我就够了」2025-04-03 | ✅ 已开 | DB `wolf_discipline_config.profit_take.enabled=true`（运行时实测）；`t_monitor._check_profit_take` |
| **C1** | 底仓 floor 穿透（**只放行"顶部阶段中轨完全止盈"**） | 「**顶部阶段**…全止盈放日线 BOLL 中轨附近，放量跌破收盘**完全止盈**」2025-05-13 | ✅ 已落地 | `t_monitor.FLOOR_BREAK_KINDS` + `_check_boll_mid_exit` 改直连执行（清仓含底仓）；`WOLF_FLOOR_BREAK=0` 可退回 |
| **C2a** | 周末/长假前避险进**执行层**（T 仓减半、底仓不动） | 「2 点半…把这两天 T 进去的仓位出来一半」+「14:35 我按刚才说的操作了」2026-08-21 | ✅ 已落地 | `t_monitor._check_weekend_hedge`（读 14:31 的状态；active∧当日∧过 14:30）；`WOLF_WH_EXEC=0` 退回提示层；`wolf_weekend_hedge_sell` 已注册为卖腿事件 |
| **C2b** | **回补腿**（周一拿回 / 避险结束补回） | 「这样周一再拿回来」2026-08-21；「万一低开=反T / 高开没吃到就不纠结」2026-08-21；「避险逻辑结束后应该补回来（个股逻辑没变）」2025-09-24 | ✅ 已落地（2026-09-14） | 新增 `wolf_hedge_refill`（纯状态+判定）+ `t_monitor._check_hedge_refill()`：只补等量、不追高（≤卖价×1.01）、负事件不补、2 个交易日窗口后放弃；走 gateway 买入通道（受 L5 闸）；设计见 `docs/wolf-hedge-refill-design.md` |
| **G0** | 死腿清理（custom_trail_sell 不再跨日结转） | 机制 2026-09-10 已删（trail_break 恒 False） | ✅ 已落地 | `_rollable()/_ROLL_SKIP_KINDS`；存量 3 条 active 已置 expired（trail 全部 18 条 expired） |
| — | **持仓口径统一为只读 stock**（t 账户暂时不使用）：纪律卖腿 + BOLL 上轨/中轨 + 去弱留强 + 指数级止损 + 逻辑时间止损 + 每日维护/AI 维护 全部走同一口径 | t 是测试账户（用户 2026-09-13/09-14 明确「持仓口径只读 stock，T 账户暂时不使用」） | ✅ 已落地 | `t_monitor._position_accounts()` / `POS_ACCOUNT` / `TMonitor._positions()`；清掉 6 处硬编码 t 的 `t_pool._get_positions()`；临时切回：`WOLF_POSITION_ACCOUNT=t` |
| **G1** | 顶部判据做宽：**放量转缩量+收黑K破5日线**（2026-01-12）/ **放量上影线 ≥2×10 日均量**（2025-05-06）/ **银保长上影+放量+大盘缩量**（2025-05-15） | 见左 | ✅ 已落地（2026-09-14） | 新增 `wolf_top_signals.py`（S1/S2/S3 + 并列条件组）；`wolf_boll_levels.market_top()` 改为**并集**（他的判据 ∨ 原 120 日分位代理），返回 `source`；动作强度分层：**仅信号触发 → 减半**（他"减仓避一下" 2026-01-12），原代理触发 → 完全止盈（2025-05-13）；开关 `WOLF_TOP_SIGNALS / WOLF_TOP_MIN_SIGNALS / WOLF_TOP_INCLUDE_LEGACY / WOLF_TOP_SIGNAL_REDUCE` |
| **G2** | 个股止盈点 = **前一波拉升幅度的 0.618 位** | 「超过或者到了 个股的前一波拉升幅度的 0.618 位 就是我的止盈点了」2026-04-23；「黄金分割只用 0.382 和 0.618」2026-03-05 | ✅ 已落地（2026-09-14） | 新增 `wolf_fib_target.py`（摆动点/前一波/0.618 位纯函数）+ `t_monitor._check_fib_target()`：前一波=买入前最近已确认"低→高"波段，止盈位=低+0.618×(高−低)，到点且有浮盈 → 卖 T 仓；开关 `WOLF_FIB_TARGET / WOLF_FIB_RATIO` |
| **G3** | 破线卖出后**"删票"**（移出观察池 + TTL） | 「最下面那根线一旦破了 卖出然后删票」2025-02-06；「破之前新低的，直接删票」2025-04-03；「这两根破了这个标我就不看了」2021-01-22 | ✅ 已落地（2026-09-14） | 新增 `wolf_ticket_ban.py`（TTL 用**交易日**，默认 13 —— 期限本身是我们的代理，代码已注明）；`t_monitor._after_sell()` 在**止损/破位/被动止盈线**成交后登记；买入侧过滤（`rotation_switch_arm` + `wolf_confirm_pick`）**打日志不静默**；开关 `WOLF_BAN_LIST` |
| **G4** | 被动止盈线「**只上移、不破不卖**」 | 「被动止盈位提高到 3373，不破不卖」2025-06-09；「不破被动止盈根本不会卖」2025-07-17；「浮盈过 100% 后设置 13 或中轨」2026-07-01 | ✅ 已落地（2026-09-14） | 新增 `wolf_passive_stop.py`（候选=近 13 日最低价；浮盈>100% 并用 MA13/中轨；**新线=max(旧,候选) 只上移**）+ `t_monitor._check_passive_stop()`：跌破且有浮盈 → 卖 T 仓，受 ④ 时点门约束；开关 `WOLF_PASSIVE_STOP` |
| **G5** | 高位/低位两套相反规则（高位**卖强留弱**、低位**留强丢弱**） | 「之前的高位方向 大科技这些…**卖强的 留弱的 拉升后都走**；之前的低位方向 AI软券商军工这些…**强的留 弱的丢**」2026-09-04 15:07 | ✅ 已落地（2026-09-14） | 口径 = **方向（主题/概念）的高低**，复用既有概念分类器 `data/position_class_result.json`（`apps/main_line/position_class.py`，每日刷新）+ `fusion_mainline.THEME_CONCEPTS` 主题→概念映射；因为该分类器整体偏 MID（实测 MID 420 / HIGH 28 / LOW 4 / 空 69），改用**相对全局基准的富集度**判高位（HIGH 个数 ≥2 且占比 ≥2×基准 5.4%），不猜。落点：新增 `backend/app/services/wolf_direction_position.py`；`position_discipline.select_weak()` 按分支排序（高位降序卖最强 / 低位升序卖最弱，**每组必留一只**）；`t_monitor._check_position_discipline` 注入 `dir_pos` 并把分支写进卖腿理由。开关 `WOLF_DIRECTION_POS` / `WOLF_DIRECTION_POS_MIN_HIGH` / `WOLF_DIRECTION_POS_SHARE_MULT` / `WOLF_POSITION_DISC_HIGH=0`（回退原统一口径）。⚠️ 实测（2026-09-14 生产数据）：14 个方向里只有 **汽车/智驾** 达高位门槛（2/10=20% ≥2×5.4%）——即 G5 平时基本不改变行为，只在真高位方向翻转 |
| **G6** | **一个票最多买 2 笔、卖 2 笔** | 「一个票最多买 2 笔 卖 2 笔 后面如果是主升浪的话越动收益越低」2025-02-07 | ✅ 已落地（2026-09-14） | 落在**唯一下单入口** `t_gateway.gateway_execute`（validate_order 之前）：`trade_cap_ok/_count_today_trades`，覆盖所有买入路径；**止损与破位保护性卖出豁免**；`WOLF_MAX_TRADES_PER_SYMBOL_PER_DAY`（默认 2；0=关） |
| **G7** | **大涨日多卖、大跌日多买** | 「大涨之日少买票，多卖票，大跌之日多买票 少卖票」2025-01-23 | ✅ 已落地（2026-09-14） | `wolf_day_rules.day_bias/tp_threshold/allow_realize_sell` + `t_monitor._index_pct_today()`：大涨日（≥1.0%）兑现门槛 ×0.67（3%→2%）；**大跌日不新增兑现类卖腿**（保护性卖出照旧）；买侧不新造"放宽买入"；开关 `WOLF_DAY_RULES` |
| **G8** | **出上影线 → 停止做 T** | 「任何时候 看见机器人板块出上影线 立马停止做T，保持 30% 机器人底仓就别动了」2025-07-17 | ✅ 已落地（2026-09-14） | 执行层停机门（`_round` 内）：最新一根**已完成**日线上影 ≥`WOLF_SHADOW_RATIO`(0.3)×全幅 → 当日跳过该标的做T买腿与兑现类卖腿（触发记 blocked），保护性卖出不受影响；日线取不到 → 不停机 |
| **G9** | **节假日反 T：早盘卖、尾盘买** | 「以后是节假日出今日…尽量做到**早盘卖 尾盘买的反T**」2025-04-29 | ✅ 已落地（2026-09-14） | `wolf_weekend_hedge.cutoff_for(kind)`：**长假前 10:00 早盘卖**（`WOLF_WH_HOLIDAY_HM`）、周末前仍 14:30；`wolf_hedge_refill.from_hm(holiday)`：**长假后回补从 14:00 起（尾盘买）**、周末后仍 09:35 |
| **G10** | 尾段状态机 | 「一旦我认为行情结束」2026-09-04 | ⛔ 不做 | 条件不可识别 → 做了就是前视 |
| **G11** | 诱空最多三次 | 2026-09-07 | ⛔ 不做 | 需盘口/分时语义，暂不可算化 |
| **A1** | **roundtrip_sell 优先级反转**：+3% 目标优先于破黄线；破黄线需"突发"确认（幅度 ≥0.5% 或连续 2 轮在黄线下） | 「**一旦突发跌破直接走**；**如果没跌破就找这半小时的高点**」2026-08-04 10:48；「至少能有吃 3-5 个点的幅度」2026-08-13 楼275/280 | ✅ 已落地（2026-09-14） | 新增 `backend/app/services/roundtrip_priority.py`（纯函数 `roundtrip_decision`）+ `t_monitor._check_roundtrip_sell` 改判；开关 `WOLF_RT_PRIORITY=target_first`（回退 `vwap_first`）/ `WOLF_VWAP_BREAK_PCT=0.005` / `WOLF_VWAP_BREAK_ROUNDS=2`；单测 `backend/tests/test_roundtrip_priority.py` 6 项 |
| **A2** | 破黄线只保留**保护语义**，不作为独立 alpha 来源 | 真 m5：33/33 条腿都会破线（`docs/exit-rules-m5-report.md` §3.1） | ✅ 按设计成立 | A1 之后破线只在目标之后触发离场（直跌保护），不再被当成择时信号；语料里与之同族的量能条件（「缩量不参与」「放量跌破收盘完全止盈」）已分别落在别处，未重复造 |
| **A3** | 破线若要当信号，须定义**确认条件**（幅度/根数/量能） | 同上 §3.3 | 🚧 口径已定、阈值未定 | 阈值扫描 `jobs/eval_roundtrip_confirm.py`（§ 报告 4.1）：0.5%/2 轮仍 26/33 走破线、均值 +0.01%；rounds 3→5 时均值 −0.04→+1.65 **说明是"破线出场越少越好"而非阈值更好**；n=33 + H1/H2 变号 → **不据此调参**，生产保持保守默认 |
| **A4** | **改成跟狼大一致**：等量换手兑现只在他的两个做 T 窗口内执行（09:45–10:00 / 14:00–14:30）；**14:00 未达标也 T 掉收工**；确认破黄线任何时候可走 | 2025-04-15 成文流程条件 2「当日只做上午 9:45–10:00、下午 14:00–14:30 这两个时间段」；2026-09-02 14:03「**2 点到了 力度不够 我先把早上博弈的先T了**…结束今天半导体做T操作」；2026-08-04 10:48「一旦突发跌破直接走」 | ✅ 已落地（2026-09-14） | `roundtrip_priority.roundtrip_decision()` 增加 `hm` + 窗口/到点判定；`t_monitor._check_roundtrip_sell` 传当前时刻、卖腿理由按分支带原话。开关：`WOLF_RT_WINDOW=0`（回 A1 旧行为）/ `WOLF_RT_WINDOWS` / `WOLF_RT_FORCE_HM=off`（只留窗口语义）/ `WOLF_RT_TIMEOUT_TOL=0.005`（"亏个手续费"容忍）。单测 12 项。**实测（n=428，`jobs/eval_roundtrip_windows.py`）：A4a ≈0（1 日 +0.01%/edge −0.9pp；2 日 −0.01%），落地理由是"忠实 + 回测无法区分"**（做 T 腿 1–2 日窗口内各口径都在 ±0.15pp、se≈0.18）；⚠️ 更正：§报告 §2 里 V3d +0.34%/edge +5.9pp 属于"**收盘** ≥+3% 才卖"的 5 日口径，**做 T 腿用不上**，不能记到 A4 头上（见 `docs/exit-rules-m5-report.md` §4.2） |
| **阶段 1** | 兑现口径变体对照 `jobs/eval_exit_rules.py`（V1 +3% / V2 破黄线 / V3 组合 / V4 做 T 前置 / V5 高低位） | plan §5 阶段 1 | ✅ **已验收（2026-09-14，n=428 × 真 m5）** | 生产腿 n=33 的结论已在 **428 条参考腿 × 真分时均价线** 上复核（`docs/exit-rules-replay-m5-report.md`；428/428 腿 m5 齐全、0 缺口）：① **破线独立离场无 alpha**（V2a/b/c/d 均值 −0.04~0.00、胜率 41–42% < 持 T+5 的 47.4%，且 428/428 必破）；② A1 的“加确认”非关键（V2d −0.02 vs V2a −0.03，噪声内）；③ +3% 止盈 = **分布变换**（胜率 +5.2pp、中位 +0.98pp，均值 −0.14pp）；④ 生产现口径 V3d +0.34%/块状 t 2.29 ≥ 旧“破线优先”V3c +0.30%/2.05 且 **H1/H2 同号** → 保留 A1。V4/V5 仍是代理口径（做 T 前置 / 个股分位），未纳入本次验收 |

### 5.1 本轮落地的验证（2026-09-14）

- 单测：`backend/tests/test_exit_floor_and_hedge.py` 6 项（底仓穿透白名单 / 避险减半量 / 执行门 / 开关 / 死腿过滤 / 账户口径），
  连同相关套件 **119 passed**；
- 部署：代码为 bind mount（宿主 `backend/app` → 容器 `/app/app`），拷文件后 `docker restart marcus-backend marcus-worker`；
- 生产实测：容器内 `profit_take.enabled=true`（`config_source=db`）、`_floor_break_enabled("wolf_boll_mid_exit")=True`、
  `_wh_exec_enabled()=True`、`_hedge_reduce_volume(20000,10000)=5000`、`wolf_weekend_hedge_sell` 已注册；
  worker 日志 `[TMonitor] ✅ 做T监控器已启动`；`custom_trail_sell` 全部 18 条 expired。
- C2b 回补腿（同日追加）：`backend/app/services/wolf_hedge_refill.py` + `t_monitor._check_hedge_refill()`，
  单测 `backend/tests/test_wolf_hedge_refill.py` 8 项；服务器自检 enabled/chase_max=0.01/window=2/from=0935、
  `trade_days_between(20260911→20260914)=1`（周五→周一=1 个交易日）、六种场景判定符合预期、
  `wolf_hedge_refill` 注册为买入事件；设计见 `docs/wolf-hedge-refill-design.md`。
- **A1 优先级反转**（同日追加，用户拍板「先做 A1」）：`backend/app/services/roundtrip_priority.py` +
  `t_monitor._check_roundtrip_sell` 改判（新增 `_vwap_break_streak` 状态）；单测 `backend/tests/test_roundtrip_priority.py` 6 项。
  **服务器实测**（`marcus-worker` 容器内直调）：`priority()=target_first`、`vwap_break_pct=0.005`、`vwap_break_rounds=2`；
  四种场景判定 = 目标+深破→`sell_target`｜0.3% 浅破→`wait`(streak 0)｜4.8% 深破→`sell_vwap`｜第 2 轮在线下→`sell_vwap`；
  `_vwap_break_streak` 状态与 `roundtrip_sell` 待卖腿联通。**回退**：`WOLF_RT_PRIORITY=vwap_first`（或 PCT=0 + ROUNDS=1 = 旧"一破就走"）。
- **A1 的验收证据**（同日追加）：`jobs/eval_roundtrip_confirm.py` → `docs/exit-rules-m5-report.md` §4.1。
  诚实结论：**加分主要在"目标优先"**；"加确认"在 n=33 上只值 +0.01pp（噪声内），阈值不可定。
- G1 顶部判据（同日追加）：`backend/app/services/wolf_top_signals.py`（S1/S2/S3 + 并列条件组）
  + `wolf_boll_levels.market_top()` 并集 + 动作强度分层；单测 `backend/tests/test_wolf_top_signals.py` 8 项；
  服务器自检：`S1=False S2=False S3=False, n=0`（as_of=20260911）、`market_top source=none / top=false`、
  `mid_break_sells` 带 `reduce_ratio/top_source`、`WOLF_TOP_SIGNAL_REDUCE` 生效。
- 阶段 1（同日）：`jobs/eval_exit_rules.py` + `docs/exit-rules-variants-report.md`（V1 +3% 57.6%/+1.29% vs 生产 51.5%/−0.13%；
  V2 的 VWAP 代理过宽 → 不作验收；428 参考腿 V1 47.4%→52.6%）。
- G2 + G4（同日）：`wolf_fib_target.py`（前一波 0.618 位）+ `wolf_passive_stop.py`（被动线只上移）；
  单测 `backend/tests/test_wolf_exit_targets.py` 6 项；自检 SH512480 0.618 位 1.0338 / SH603259 155.06、被动线候选 0.935/149.70。
- G3 + G6 + G7 + G8 + G9（同日）：`wolf_ticket_ban.py`（删票 TTL=13 交易日，买入侧过滤带日志）、
  `t_gateway` G6 笔数护栏（默认 2，止损/破位豁免）、`wolf_day_rules.py`（G7 日型门槛 / G8 上影线停机）、
  G9 节假日时点（长假前 10:00 卖 / 长假后 14:00 回补）；单测 `test_wolf_ticket_ban.py` 7 项 /
  `test_g6_trade_cap.py` 5 项 / `test_wolf_day_rules.py` 7 项 / `test_g9_holiday_timing.py` 7 项，全部通过。
- 🐛 G9 顺带修一个**静默失效**：`wolf_weekend_hedge._hhmm` 只认 "HH:MM"，传 "1005" 会静默返回 0 → 时点门失效；已兼容 HHMM。
- ⚠️ 已知与本轮无关的测试收集错误：`backend/tests/test_marcus_trade_notify.py` 缺 `workspace_detector` 模块（与本次改动无关）。

- **G5 方向高低位**（同日追加）：`backend/app/services/wolf_direction_position.py`（方向→概念→position 聚合 + 富集门槛
  + 48h 新鲜度护栏 + 缺失即 UNKNOWN）+ `position_discipline.select_weak()` 分分支 + `t_monitor` 注入 `dir_pos`；
  单测 `backend/tests/test_direction_position_g5.py` 10 项（含开关回退、分支内分化门槛、"每组必留一只"）。
  **服务器实测**：`/app/data/position_class_result.json` 新鲜度 2.0h、全局 HIGH 基准 5.37%；
  `direction_position('汽车/智驾')=HIGH`（2/10=20% ≥ 2×基准）、其余方向 MID；`branch_of('汽车/智驾')=high_sell_strong`；
  `select_weak` 场景 = 高位持仓卖最强(AAA +9.0%)、低位持仓卖最弱(CCC +1.0%)，理由文本含两支原话；
  重启后 TMonitor 正常启动、无异常（当前 stock 账户有效持仓 1 < 门槛 3 → 暂不动作，符合预期）。

- **回归状态（2026-09-14 逐文件跑 backend/tests）**：与本次改动相关的文件全绿 ——
  `test_roundtrip_priority.py` 6、`test_direction_position_g5.py` 10、`test_position_discipline_rebound.py` 10、
  `test_exit_floor_and_hedge.py` 9、`test_g9_holiday_timing.py` 7、`test_p2_4_stop_close_confirm.py` 16、
  `test_g6_trade_cap.py` 5、`test_wolf_top_signals.py` 8、`test_wolf_day_rules.py` 7 等。
  **预存在失败（与本次改动无关：这些测试文件都不 import t_monitor/position_discipline/wolf_direction_position）**：
  `test_dca_carrier.py`（`DCA_CARRIER_DEFAULTS["588000"].mode` 是 fixed_combo，测试期望 sector_selection）、
  `test_api_responsiveness.py::test_golden_pit_router_handlers_run_in_threadpool`、
  `test_main_wave_analyzer.py::TestMainWaveAnalyzer::test_600613_shenqi_red_flags`；
  另有 3 个文件在本地**超时**（需要外部网络/DB：`test_daily_decision.py`、`test_golden_pit_paper_execution.py`、
  `test_golden_pit_sector_service.py`），以及 `test_marcus_trade_notify.py` 的既有 collection error
  （`No module named 'workspace_detector'`）。整仓 `pytest backend/tests` 一次跑会挂在那 3 个网络文件上。

- **A4 做 T 时间窗 + 2 点决断**（同日追加，用户"改成跟狼大一致"）：`roundtrip_priority.py` 扩到
  `roundtrip_decision(cur, buy_avg, avg, up, streak, hm)`（窗口内兑现 / 14:00 到点收工 / 破线保护优先）；
  `t_monitor._check_roundtrip_sell` 传 `hm`；单测 `backend/tests/test_roundtrip_priority.py` **12 项**（含窗口闸、
  到点收工、浮亏容忍、保护优先、两个开关回退）。
  **服务器实测**：`window_enabled=True windows=09:45-10:00,14:00-14:30 force=14:00 tol=0.005 priority=target_first`；
  八种场景判定 = 10:30 达标→`wait`(不在窗口)｜09:50/14:10 达标→`sell_target`｜14:00 +1.1%→`sell_timebox`｜13:55 +1.1%→`wait`｜
  14:05 −0.3%→`sell_timebox`｜14:05 −2%/−3%→`wait`(超过容忍，交给保护腿)。回退：`WOLF_RT_WINDOW=0`。

### 5.2 G1 离线体检（2026-09-14，上证 2025-04-01 → 2026-09-11，355 个交易日）

| 判据 | 命中天数 | 占比 | 信号日之后 5 日（指数） | 对照（其余日） |
|---|---|---|---|---|
| S1 放量转缩量+收黑K破5日线（2026-01-12） | 3 | 0.8% | — | — |
| S2 放量上影线 ≥2×10 日均量（2025-05-06） | **0** | 0% | — | — |
| S3 银保长上影+放量+大盘缩量（2025-05-15） | 28 | 7.9% | — | — |
| **≥1 条（当前默认口径）** | **31** | **8.7%** | 均值 **−0.04%** / 中位 +0.19% / 胜率 **52%** | 均值 +0.32% / 中位 +0.45% / 胜率 61% |
| ≥2 条（原设想的"并列条件组"） | **0** | 0% | — | — |
| 原代理：120 日区间分位 ≥0.8 | 158 | **44%** | 均值 +0.30% / 胜率 60% | ≈ 无区分度 |

- **结论 1**：他的 T+5 方向**是对的**（信号日后 5 日更弱：−0.04% vs +0.32%，胜率 52% vs 61%），但 **n=31 偏小、未做块状 t** → 只作"方向性支持"，验收仍需阶段 1 的正式回测。
- **结论 2**：把 S1/S2/S3 当"≥2 条并列"在 355 天里**一次都不触发** → 会变成新的休眠规则 → 默认改成 **`WOLF_TOP_MIN_SIGNALS=1`（他的任一判据成立）**。
- **结论 3**：**S2 在指数上一次都没出现**（指数成交量极少达到 10 日均量的 2 倍）→ 他那句更可能说的是**个股/板块**口径；指数层保留但实际由 S1/S3 主导。
- **结论 4**：原代理（120 日分位）**44% 的交易日都算"顶部"**、且之后 5 日并不更弱 → 原来那道门槛**过松、近乎噪声**；G1 的实际意义是**用他的判据替换/并列这道噪声门**。
- 仍待做：C1 的中轨腿当前 `source=none`（代理不满足、信号也未命中）→ 仍休眠，等信号命中才动作。
