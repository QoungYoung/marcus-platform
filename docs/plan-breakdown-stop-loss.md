# 破位止损开发方案（BreakStop v1）

> 交接文档，自包含。目标是把“破位止损”从散落在 6 个模块里的规则，收敛成**一个可解释、可回放、可分级启用**的编排层。
> 阅读顺序：§0 一页结论 → §1 语料判据 → §2 现状盘点 → §3 设计 → §4 实施 → §5 验收 → §7 红线。
> 相关文档：`docs/stop-layer-audit.md`（六层量化对照）、`docs/plan-exit-alignment.md`（退出层总计划）、`docs/ai-agent-lessons-from-b-rebound.md`（agent 开发教训）、`docs/wolf-trading-playbook-taxonomy.md`（X3 破位/止损分类）。

---

## 0. 一页结论

**不做“第 7 条止损规则”，做“统一编排层”。**

| 层 | 内容 | 是否自动执行 |
|---|---|---|
| **硬闸（P0）** | 指数级破位：收盘跌破大级别结构（MA60/144/200 ≥2 根，或平台/趋势线/前低） | ✅ 唯一自动执行层 |
| **软层（P1）** | 板块/个股破位：平台、趋势线、颈线、缺口、MA13/34、放量跌破前一日红 K | ⚠️ 默认只提示；验证后才减仓 |
| **时间层（P2）** | 13 日未碰前高 / 波段低点 −3% 未收回 | ⚠️ 默认提示（audit 显示负贡献） |
| **复用层** | ②趋势线、④时点门、E6 收盘确认、G4 被动止盈 | 保持现状，不重写 |
| **账本** | `break_events` + `break_actions` + 影子模式 + 回放 | 只记录，不下单 |

**一句话**：**指数破位是唯一的“硬止损”，个股破位是“减仓/提示”，不是“清仓信号”。**

> ⚠️ **规则来源审计**：哪些是狼大原话、哪些是代理参数、哪些是我自设的工程规则，见
> [`docs/break-rule-provenance.md`](break-rule-provenance.md)。**不要把代理/工程说成原话。**

### 0.1 决策记录（2026-10-23，用户已拍板）

| # | 决策 | 结果 | 落地约束 |
|---|---|---|---|
| D1 | 硬闸范围 | **指数级破位 = 唯一自动止损；个股级默认只提示** | `WOLF_BREAK_MODE` 最高到 `execute_index`；`execute_all` 需二次批准 |
| D2 | 指数口径 | **只用上证（000001.SH）** | 不引入中证 1000/平均股价作为硬闸；可作为展示列，不参与动作 |
| D3 | B3 目标仓位 | **默认 50%、深度 30%（cap，不是 floor）；down-wave 0-30% cap，默认 0** | `WOLF_BREAK_INDEX_CAP=0.50`、`..._DEEP=0.30`、`..._DOWN_CAP=0.30`、`..._DOWN_DEFAULT=0.0` |
| D4 | 个股自动层 | **先回测，`WOLF_BREAK_STOCK_EXEC=0` 保持不变** | P5 回测出证据后再议；未证明有效前不自动减仓 |
| D5 | Agent 层 | **做完规则再看是否需要** | P7 暂不开发；先交付 P1-P6 |
| D6 | P4 运行模式 | **不做 shadow**：`alert` 真实落库 + 真实推 QQ，但**不下单**；`execute_index` 等 P5 回测通过 | `WOLF_BREAK_MODE=alert`；`break_stop_1450` 任务 `enabled: false`，回测后再开 |

这与 `stop-layer-audit.md` 的结论一致：个股级止损在这批样本上是负贡献（+2.40% → +0.79%，止损正确率 46%，止损后 10 日平均反弹 +6.72%）；指数级 MA60 是唯一同时改善均值和左尾的层（+1.29%，最差 5 笔 −16.8% vs 不止损 −26.2%）。

---

## 1. 语料判据（带日期，全部为原话可核对）

> 读法遵循 `docs/ai-agent-lessons-from-b-rebound.md` §10：剥掉 `[quote]`、用日期锚定、取最新/最完整版本、抓“如果 X 就 Y”的触发句、不把别人的提问当他的观点。

### 1.1 什么算“破位”（5 条）

| # | 判据 | 原话（日期） | 可算化 |
|---|---|---|---|
| D1 | **收盘口径，盘中不算** | 「盘中跌破不算。盘中跌破不算。盘中跌破不算。」（2025-11-21） | 只用收盘价；盘中只记录 B0 |
| D2 | **放量跌破直接算；缩量跌破等反抽不过颈线** | 「缩量跌破不算 缩量是要看反弹是否过颈线 如果不过才算 放量跌破就直接算」（2026-03-04） | 量比 ≥ `VOL_DIRECT` → 直接确认；< `VOL_SHRINK` → 进入“等反抽”状态 |
| D3 | **有效跌破 = 收盘跌破 + 未收回** | 「跌破趋势…只要没有站回趋势线上，也就是 3922，都视为跌破了趋势」（2025-11-23）；「13 日内跌破波段低点的 −3% 没有收回 直接止损」（2026-03-05）；「有效跌破 22 或者跳空到 22 以下那直接出场」（2016-06-24） | `close < level` ∧ 之后 N 日未收盘站回 → confirmed |
| D4 | **多级别：指数 / 板块 / 个股** | 「以板块指数为准」（2026-05-06）；「只看指数大级别…转为下跌 1 浪就止损」（2026-08-27） | 三个 level domain，分开计算、统一仲裁 |
| D5 | **破位 ≠ 卖出**（要看段位/位置） | 4-3 筑底、下方有 3800 支撑时，「跌破了 按计划打入」（2026-08-24）；「全场只有半导体尾盘放量…我这都不敢啃，那还玩什么」（2026-08-03） | 段位/位置前置；build 段破位可只观察甚至低吸 |

### 1.2 破位后做什么（分级，不是一刀切）

| 场景 | 原话（日期） | 动作 |
|---|---|---|
| 指数收盘破 3922 / 3900 | 「收盘跌破 3922，我就 40% 仓位；收盘跌破 3900，剩 20% 仓位。盘中跌破不算。」（2025-11-21） | 组合目标仓位 40% / 20% |
| 指数 3888 未站上 / 3850 破 / 3816 企稳 | 「3888 收盘没站上 50% 仓位；3850 收盘跌破 35% 仓位；3816 企稳 加回 50%；站稳 3922-3930 70% 仓位」（2025-12-03） | 点位→仓位表 |
| 指数放量破 3828 + 30 年债涨 + 外资流出 | 「放量跌破…同时 30 年国债涨，外资大幅流出 降低到 50% 仓位」（2025-09-17） | 多条件确认后降仓 |
| 个股收盘跌破平台 | 「跌破平台我一定减仓。我只是想活下去」（2026-01-29）；「收盘跌破平台减仓是为了被动止盈、保收益」（2026-01-29） | 减仓/被动止盈，不是清仓 |
| 个股收黑 K 跌破 5 日线 | 「收黑 K 跌破 5 日线…减仓避一下」（2026-01-12） | 减仓 |
| 成趋势后破 60 日 / 34 日带量 | 「60 日是我的底线，甚至没站稳 34 日带量下穿的我都会砍掉」（2021-01-28） | 趋势线层：exit / reduce |
| 建仓初期跌破波段低点 −3% 未收回 | 「13 日内跌破波段低点的 −3% 没有收回 直接止损」（2026-03-05） | 时间/逻辑止损（默认提示） |
| 指数大级别转下跌 1 浪 | 「只看指数大级别 如果不走大 5 浪而转为下跌 1 浪就止损」（2026-08-27） | 系统性硬闸 |
| 放量跌破前一日红 K（量能多一倍） | 「放量跌破昨天的红 K 量能多一倍 就当昨天没涨就行了」（2026-05-21） | 个股/ETF 的放量破位确认 |

### 1.3 什么时候“不该卖”（4 条，必须先于动作判断）

| # | 条件 | 原话（日期） | 实现 |
|---|---|---|---|
| N1 | **盘中未收盘** | 见 D1 | B0 只记录，不下单 |
| N2 | **缩量破位未确认** | 见 D2 | 进入 `SHRINK_BREAK`，等反抽不过颈线 |
| N3 | **地量 / 急跌 / 低位支撑** | 「急跌不割肉」（2026-01-14）；「地量不割肉」（2026-08-21） | 地量/急跌标记 → 只提示，不自动卖 |
| N4 | **时点门 13:00-14:30** | 「每天的止损绝对不应该是下午 1 点到 2 点半…要么早上卖 要么你尾盘卖」（2026-03-23） | 复用 `_stop_time_ok()` |

### 1.4 判例回归集（开发时先写这 10 条）

| # | 日期 | 级别 | 判据/动作 | 预期 |
|---|---|---|---|---|
| C1 | 2025-11-21 | 上证 3922/3900 | 收盘跌破 → 40%/20% | B3；target 0.40 / 0.20 |
| C2 | 2025-11-23 | 上证 3922 | 未站回 → 无趋势 | confirmed；降趋势仓 |
| C3 | 2025-12-03 | 上证 3888/3850 | 未站上/跌破 | B3；0.50 / 0.35 |
| C4 | 2025-09-17 | 上证 3828 | 放量破 + 债涨 + 外资流出 | B3；0.50 |
| C5 | 2026-01-29 | 个股平台 | 收盘跌破平台 | B2；reduce half |
| C6 | 2026-03-04 | 个股/指数 | 缩量跌破 vs 放量跌破 | shrink → 等反抽；volume → 直接确认 |
| C7 | 2026-03-05 | 个股 | 13 日 / 波段低点 −3% 未收回 | B4；alert（可选 reduce） |
| C8 | 2026-05-06 | 板块指数 | 突破后以收盘点为止损 | B2；sector 优先 |
| C9 | 2026-05-21 | 半导体 ETF | 放量跌破前一日红 K（2× 量） | B2；reduce |
| C10 | 2026-10-15 | 上证 3764 | 有效跌破 → 熊市确认 | B3-3；cap 0-30%，默认 0 |

---

## 2. 现状盘点：已经有什么、缺什么

### 2.1 已有六层（不要再造一遍）

| 层 | 模块 | 状态 | 备注 |
|---|---|---|---|
| ① 建仓初期逻辑止损 | `wolf_early_stop.py` | 生产开（`WOLF_EARLY_STOP=1`） | 13 日 / 波段低点 −3%；“无利空”前提 |
| ② 趋势线法 | `wolf_trend_stop.py` | 生产开（`WOLF_TREND_STOP=1`） | MA5/13/34/60；`WOLF_TREND_VOL_RATIO=1.0`、`WOLF_TREND_WEAK_NO_VOL=1` |
| ③ 指数大级别 | `wolf_context.index_level_stop` + `t_index_break.py` | 浪型层开；MA 破位默认关 | `WOLF_INDEX_BREAKDOWN=0`；`WOLF_STOP_BY_INDEX_BREAK=0` |
| ④ 时点门 | `t_monitor._stop_time_ok` | 开 | 13:00-14:30 不执行 |
| ⑤ 收盘确认 / 假跌破 | `t_stop_loss_guard.evaluate_stop` + `t_monitor._stop_close_confirm` | 开（`WOLF_STOP_CLOSE_CONFIRM=1`） | 收盘确认、收回幅度、缩量贴支撑、分钟企稳 |
| ⑥ 被动止盈 | `wolf_passive_stop.py` | 开（`WOLF_PASSIVE_STOP=1`） | 13 日低点/MA13/BOLL 中轨，只上移 |

### 2.2 已有代码资产（可直接复用）

| 资产 | 位置 | 作用 |
|---|---|---|
| 支撑/压力位 | `support_resistance.compute_levels` | 20/60 日平台、局部峰谷、MA20/60、整数关 |
| 破支撑字段 | `t_monitor._build_snapshot` 的 `break_support` | 首次破 MA13/34 + 量比 >1.2（`WOLF_BREAK_SUPPORT_TREND=1`） |
| 量能分层 | `t_monitor._settle_pullback_sell` | 缩量破位 → 反抽/尾盘确认 |
| 黄线离场 | `t_monitor` 条件 252 + `WOLF_VWAP_SELL_B5` | 分时 VWAP |
| 指数 MA 破位 | `t_index_break.index_breakdown` | 收盘破 MA60/144/200 ≥2 根、连续 N 日 |
| 指数浪型止损 | `wolf_context.index_level_stop` | `level == down`，带新鲜度护栏 |
| 组合目标仓位 | `t_capacity.level_target_pct` / `base_floor_pct` / `WOLF_TARGET_TABLE` | 点位→仓位表 |
| 执行 | `t_gateway.gateway_execute` | 唯一下单路径；`is_stop_loss=True` |
| 账户/底座 | `t_monitor._check_stop_loss` | 持仓、可卖量、底仓 floor、T+1 |
| 事件/告警 | `alert_hub`、`t_triggers`、`stop_loss_log` | 记录与推送 |

### 2.3 七个缺口（本方案要补的）

| 缺口 | 现状 | 后果 |
|---|---|---|
| **G1 类型化的 level** | `support_resistance` 返回的是未分类价格；`break_support` 用“最近支撑”；`t_index_break` 只有 MA | 无法回答“破的是平台、趋势线还是颈线”，优先级/动作无法区分 |
| **G2 确认状态机** | 各模块各自判断盘中/收盘/缩量；没有统一状态 | 同一天同一标的可能被不同模块判成不同结论；无法回放 |
| **G3 跨级仲裁** | 指数/板块/个股各自触发、代码顺序决定优先级 | 可能指数破位清仓 + 个股破位再清一次；或个股先卖、指数后降仓 |
| **G4 动作矩阵** | 只有单条 stop_price / reduce 比例；指数破位没有“组合目标仓位”的动作 | 指数破位只能靠个股逐只止损，卖不出“降到 30%”的组合效果 |
| **G5 事件账本/影子** | 只有成交日志 `stop_loss_log`，没有“破位事件”本身 | 无法统计假破位率、无法做影子/回放 |
| **G6 板块级** | 只有个股/指数；`t_index_break` 是上证 | 他明确说“以板块指数为准”，现在做不到 |
| **G7 执行去重** | `stop_loss_monitor` 默认 `STOP_LOSS_DYNAMIC_ONLY=1`（只展示）；`t_monitor` 卖；还有 `position_tier_monitor`、`wolf_passive_stop` | 多入口，存在重复卖/漏卖风险 |

### 2.4 数据现状与坑

| 数据 | 覆盖 | 用途 | 坑 |
|---|---|---|---|
| `data/指数数据/index_daily/000001.SH.csv` | 2016-01-04 → 2026-09-29 | 指数 MA/平台/趋势线 | 只有收盘 OHLC + amount，无成交额分时；单位是点 |
| PG `mkt_bars_daily` | 2024-11-01 → 2026-09-11（约 247 万行） | 个股/ETF 日线回测 | 与本地 SQLite 口径可能不同；复权要先对齐 |
| 本地 `data/mkt_bars_local.db` | 2024-11-01 → **2025-12-31** | 本地快速分析 | **2026 年数据没有**，不要拿它跑 2026 回测 |
| 腾讯 `qt.gtimg.cn` | 实时 | 盘中 quote / VWAP | 单点数据源；失败必须 non-zero/告警，不能拿昨日冒充今日 |
| 中证 1000（`sh000852`） | 实时/日线 | “平均股价/黄线”代理 | 不是真正的等权平均股价，只能做方向语义 |
| 30 年国债 / 北向 | 未接入 | 指数破位的额外确认 | v1 **不纳入**硬判据，避免数据不可得导致空转 |

---

## 3. 设计

### 3.1 架构

```
┌────────────────────────────── 事实层（程序，不含结论） ──────────────────────────────┐
│ index_bars / stock_bars / sector_bars / quote / volume_baseline / wave_state / position │
└──────────────────────────────────────┬───────────────────────────────────────────────┘
                                       ▼
                    ┌──────────────────────────────────┐
                    │ BreakLevelRegistry（类型化、PIT） │
                    │ platform / trend / neckline / gap │
                    │ / ma / boll / prior_high / vwap   │
                    └──────────────────┬───────────────┘
                                       ▼
                    ┌──────────────────────────────────┐
                    │ BreakDetector（确认状态机）       │
                    │ intraday → close → confirmed      │
                    │ / shrink → rebound_fail           │
                    │ / reclaimed → false               │
                    └──────────────────┬───────────────┘
                                       ▼
                    ┌──────────────────────────────────┐
                    │ BreakContext（跨级仲裁）          │
                    │ index + sector + stock + position │
                    │ + wave_op + profit cushion        │
                    └──────────────────┬───────────────┘
                                       ▼
                    ┌──────────────────────────────────┐
                    │ BreakActionPolicy（纯函数矩阵）    │
                    │ B0..B4 → target_pct / reduce      │
                    └──────────────────┬───────────────┘
                                       ▼
        ┌──────────────────────────────┴──────────────────────────────┐
        │ BreakStopService（编排：事件账本 / 告警 / 影子 / 回放）        │
        └───────────────┬───────────────────────────────┬─────────────┘
                        ▼                               ▼
             t_monitor（卖腿执行）              break_agent（可选，只评估不下单）
                        ▼
             t_gateway.gateway_execute（唯一下单路径）
```

**边界**：
- 程序负责**算事实、判资格、给动作**；agent 只负责**解释性质**（真破位/假破位/看不清），不能造价格、不能直接下单、不能越过硬闸。
- `stop_loss_monitor` 保持“展示/距离”角色，不再新增卖出逻辑；卖出一律走 `t_monitor` → `t_gateway`。
- 任何“为了指标好看”的规则，先拿 §1.4 的判例过一遍，误杀就说明测的不是他的东西。

### 3.2 领域模型

```python
BreakLevel = {
  "level_id": "SH600000:platform_low:20260814:9.85",  # 语义键，不用浮点
  "symbol": "SH600000",
  "domain": "stock|sector|index",
  "type": "platform_low|trendline|neckline|gap_lower|ma13|ma34|ma60|ma144|ma200|boll_mid|prior_day_high|prior_day_low|swing_low_minus_3|vwap",
  "price": 9.85,
  "asof": "20260814",
  "lookback": 60,
  "touches": 3,
  "priority": 60,
  "source": "mkt_bars_daily|index_csv|tencent",
  "meta": {"slope": -0.001, "window": [7, 60], "gap_date": "20260731"}
}

BreakSignal = {
  "level_id": "...", "phase": "intraday|close",
  "status": "watch|intraday_break|close_break|shrink_break|rebound_fail|confirmed|reclaimed|false",
  "close": 9.72, "low": 9.68, "vol_ratio": 1.42,
  "confirmed_at": "20260814", "reclaim_deadline": "20260818",
  "reason": "收盘 9.72 < 平台低 9.85，量比 1.42"
}

BreakContext = {
  "asof": "20260814", "mode": "live|close|replay",
  "index": {...}, "sector": {...}, "stock": {...},
  "wave_op": "build|t_only|side|defense|exit",
  "position": {"cost": 10.1, "pnl_pct": -3.8, "weight": 0.12,
               "t_volume": 2000, "base_volume": 8000, "sellable": 10000},
  "risk_flags": {"volume_dry": false, "limit_down": false, "negative_event": false}
}

BreakAction = {
  "severity": "B0|B1|B2|B3|B4",
  "action": "observe|alert|reduce_t|reduce_half|reduce_two_thirds|cap_50|cap_30|cap_0_30|clear|block_new_buy",
  "target_pct": 0.30, "reason": "...", "invalidation": "收盘站回 3922",
  "sell_order": ["weakest", "most_liquid"]   # B3 组合降仓时的卖出顺序
}
```

### 3.3 Level Registry：类型、优先级、公式

| type | 定义 | 回看 | 优先级 | 备注 |
|---|---|---|---|---|
| `index_major` | 上证收盘跌破 MA60/144/200 ≥2 根 | 200 | 100 | 复用 `t_index_break` |
| `index_platform` | 上证 20/60 日平台低 / 摆动低 | 20/60 | 95 | 收盘破 + 量比 |
| `index_trendline` | 上证 60 日线性回归下轨 | 60 | 90 | 斜率 < 0 才有效 |
| `sector_platform` | 板块指数 20/60 日平台低 | 20/60 | 80 | 板块指数/ETF 代理 |
| `sector_trendline` | 板块指数 34/60 日回归下轨 | 34/60 | 75 | — |
| `stock_platform` | 个股 20/60 日平台低 / 箱体低 | 20/60 | 60 | 需 ≥2 次触碰 |
| `stock_trendline` | 个股 13/34/60 日回归下轨 | 13/34/60 | 55 | 复用 `wolf_trend_stop` 的线 |
| `stock_neckline` | W 底颈线 / M 顶颈线 | 20/60 | 55 | 摆动点配对 |
| `gap_lower` | 最近未回补向下缺口下沿 | 60 | 50 | 缺口日 + 幅度 |
| `ma13/34/60` | 对应均线 | 13/34/60 | 40 | `break_support` 已用 |
| `boll_mid` | 日线 BOLL 中轨 | 20 | 35 | “破中轨完全止盈”（2025-05-13） |
| `prior_day_high` | 顶部阶段上一日最高（支撑） | 1 | 30 | 2025-05-13「每一天的支撑位都是上一天的最高位」 |
| `prior_day_low` | 前一日低点（不破持有 / 放量破+3×15min不收回减仓） | 1 | 35 | 2026-02-05 / 2025-12-11；3×15min 确认在 P2 |
| `swing_low_minus_3` | 建仓日锚定的波段低点 ×0.97 | 13 | 30 | `wolf_early_stop` |
| `vwap` | 当日累计 VWAP | 日内 | 20 | 只用于 T 仓/黄线，不用于底仓止损 |

**PIT 规则**：
- `asof` = 最后一个**已完成**交易日；盘中 level 只用 T-1 及更早的日线 + 当日已完成的 m5。
- `level_id` 用 `symbol:type:asof:price_semantic`，不用浮点价格做唯一键。
- 每次计算都把 `asof`、`source`、`lookback`、`touches` 写入事件；回放用 `asof <= 回测日`。
- 数据不足（< lookback + 20 根）→ `level=None`，不猜、不卖。

### 3.4 Detector：确认状态机

```
NONE
 │ price < level（盘中）
 ▼
INTRADAY_BREAK（B0，只记录/提示，不卖）
 │
 ├─ 收盘 ≥ level ──────────────► RECLAIMED（假破位，记录）
 │
 └─ 收盘 < level
      │
      ├─ vol_ratio < SHRINK ───► SHRINK_BREAK（等反抽）
      │     ├─ 反抽到 level 附近不过 → REBOUND_FAIL → CONFIRMED（B2）
      │     └─ N 日内收盘站回 level → RECLAIMED
      │
      └─ vol_ratio ≥ DIRECT ───► CONFIRMED（B2/B3）
            ├─ 次日收盘站回 → RECLAIMED（假破位；记录“止损正确率”）
            └─ 未站回 → CONFIRMED 持续
```

**参数**（全部可配，先保守）：

| 参数 | 默认 | 含义 | 依据 |
|---|---|---|---|
| `VOL_DIRECT` | 1.2 | 放量直接确认（日线量比 / 同日累计量比） | “放量跌破就直接算”（2026-03-04）；“量能多一倍”（2026-05-21）是更严的 2.0，作为 `VOL_STRONG` |
| `VOL_SHRINK` | 0.8 | 缩量分支阈值 | “缩量跌破不算” |
| `VOL_STRONG` | 2.0 | “量能多一倍”强确认 | 2026-05-21 |
| `RECLAIM_DAYS` | 2 | 收盘站回视为假破位的窗口 | “没有收回”（2026-03-05） |
| `BREAK_BUFFER` | 0.3% | 收盘价需低于 level 的缓冲，防贴线抖动 | 工程护栏（非语料） |
| `LEVEL_MIN_TOUCHES` | 2 | 平台/趋势线至少触碰次数 | 工程护栏 |
| `INTRADAY_ONLY` | true | 盘中只记录 B0 | 2025-11-21 ×3 |

**字段语义**（P2 输出，P3 直接用）：
- `below` = 收盘/现价 < level（原始口径）；
- `below_buffer` = 收盘 < level×(1−buffer)（严格口径，默认 0.3%）；
- `confirmed` = `status ∈ {confirmed, rebound_fail}`；
- `m15_no_reclaim` = 最近连续 ≥3 根 15min 收盘 < level（无数据为 None）；
- **3×15min 可越过缓冲带**：收盘已微破 + 3 根 15min 不收回 + 量比非缩量 → `confirmed`（2025-12-11 判例）。

**时间口径**：
- `mode=live`：14:40 预览（不下单）、14:55 执行窗口（用现价近似收盘，需 `BREAK_BUFFER`）、15:05 官方收盘记录。
- `mode=close`：只用收盘价，产出次日计划。
- `mode=replay`：只用 `asof` 及以前的数据；**禁止**用次日 reclaim 判断当日动作。
- 每天 `asof` 固定；跨日重算不得改写历史事件。

### 3.5 Action Policy：严重度 × 段位 × 盈亏垫

**严重度**：

| 级别 | 触发 | 默认动作 |
|---|---|---|
| B0 | 盘中破位 / 未收盘 | 记录 + 提示，不卖 |
| B1 | 收盘破次要位（MA13/34/BOLL/缺口），量比 < 1.0 | 提示；T 仓可自行减 |
| B2 | 收盘破平台/趋势/颈线，量比 ≥1.2；或缩量后反抽不过 | 减半（T 优先，底仓视段位） |
| **B3-1 结构破位** | 上证收盘破 **1 个**大级别结构（平台/趋势/MA60/144/200）+ 量比 ≥1.2 | cap 50%，卖弱留强，禁新开 |
| **B3-2 深度破位** | 上证收盘破 **≥2 根**大级别 MA，或破平台/趋势 + 量比 ≥1.2；或系统性事件（加息+无对冲） | **cap 30%**（不是必须留 30%） |
| **B3-3 down-wave** | `wave.level == down` 或 `operation == exit` | **cap 0-30%，默认 0**；有短线/题材仓时才允许到 30%；禁新开 |
| B4 | 13 日未碰前高 / 波段低点 −3% 未收回 | 默认提示；可选减半 |

> **30% 的语义（2026-10-23 用户拍板 A）**：30% 是 **cap**，不是 floor。
> 2025-11-21「3922→40%、3900→**20%**」证明可以低于 30%；2026-01-17「下跌趋势就不做」说明 down-wave 默认是 0，30% 只给短线仓。
> 因此 `exit` 行写 `cap 0-30% / clear`，不再写“固定 30%”。

**动作矩阵**（`wave_op` 来自 `wave_state`；`pnl` 是浮盈；`pos_class` 是高位/低位）：

| wave_op | B1 | B2 | B3-1 | B3-2 | B3-3 down-wave | B4 |
|---|---|---|---|---|---|---|
| `build`（4-3 筑底/主升初） | alert | alert；仅 `pos_class=high` 且 `pnl>0` → reduce 1/3 | **cap 50% + 暂停新开**；指数在支撑上方、缩量 → 只 alert | cap 30% | **cap 0-30%，默认 0 + 禁新开** | alert |
| `t_only` | alert | reduce_t | cap 50% | cap 30% | cap 0-30%，默认 0 | alert |
| `side` | alert | reduce_half | cap 30-50% | cap 30% | cap 0-30%，默认 0 | reduce_t |
| `defense` | alert | reduce_half | cap 30% | cap 30% | cap 0%，禁新开 | reduce_half |
| `exit`（下跌 1 浪） | reduce_t | reduce_two_thirds | cap 0-30% | cap 0% | cap 0%，禁新开 | clear |

**点位→仓位表（`target_override`）**：`t_capacity.level_target_pct()` 的结果可以传入 P3，**只能降低 cap，不能抬高**。
例如 C1「3922→40%」是 B3-1（cap 50）被 override 降到 40%；C3「3850→35%」同理。
低于档位时 `action=cap_custom`，`target_pct` 为实际值；档位本身仍由 `severity` 表达。

**B3 三档的语料依据**：

| 子档 | 触发 | 目标 | 原话 |
|---|---|---|---|
| B3-1 | 1 个大级别结构 + 量比 ≥1.2 | cap 50% | 2025-12-03「3888 收盘没站上 → 50%」；2025-11-23「隔夜仓不超过 50%」 |
| B3-2 | ≥2 根大级别 MA 或平台/趋势 + 量比 ≥1.2；系统性事件 | cap 30% | 2025-09-07「放量黑 K 当天减到 30% 内」；2025-10-12「跌破 3731 收盘→剩 30%」；2026-09-17「无逆周期→30% 以下」 |
| B3-3 | `wave.level == down` / `operation == exit` | cap 0-30%，默认 0 | 2026-01-17「下跌趋势就不做」；2026-08-27「转下跌 1 浪就止损」；2025-11-21「3900→20%」 |

**注意**：B3-3 的“30%”只在**有短线/题材仓**时成立（2025-11-23「剩下 30% 仓位做短线热点」）；没有短线仓时默认 0，这才符合「下跌趋势就不做」。

**盈亏垫修正**：
- `pnl > 20%`：B2 允许减半（被动止盈）；B3 时若该仓相对强度已转弱，可优先减它（锁利润+降风险）。
- `-10% < pnl ≤ 0`：B2 只减 T，不砍底仓（“急跌不割肉”）。
- `pnl ≤ -10%` 且无指数破位：只 alert，不自动卖（audit：个股止损负贡献）。
- `pnl ≤ -10%` 且 B3：按组合目标降仓，优先卖最弱/流动性最好的，不逐只清。

**卖出顺序（B3）**：先卖“相对强度最弱 + 流动性好”的；盈利仓只在“强转弱”时优先减。不按持仓顺序无差别卖；也不因为“有利润”就先砍最强的。复用 `t_monitor._check_position_discipline` 的“去弱留强”口径，但方向相反：系统性降仓时卖弱留强。

### 3.6 编排与执行：单一责任人

| 角色 | 负责 | 不负责 |
|---|---|---|
| `BreakStopService` | 计算 levels/signals/actions；写 `break_events`/`break_actions`；发 QQ | 不直接下单 |
| `t_monitor._check_break_stop` | 读 `break_actions`，按持仓/可卖量/底仓 floor/T+1 执行卖腿 | 不重新判破位 |
| `t_gateway.gateway_execute` | 唯一下单路径；`is_stop_loss=True`；幂等 | — |
| `stop_loss_monitor` | 展示距离/动态离场 | 不再自动卖 |
| `daily_decision` L6 | 把当日破位状态/动作写进决策产物 | 不重复计算 |
| `position_tier_monitor` | 仓位档位/纪律 | 不重复止损 |

**幂等键**：`(asof, symbol, level_id, action)`；`break_actions` 唯一约束；重复执行直接返回已有 `trigger_id`。
**防重复卖**：同一 `(symbol, asof, action)` 只允许一笔；`t_triggers` 里已有 `break_stop` 当日记录则跳过。
**T+1 / 底仓**：复用 `get_sellable_ledger` + `base_floor_shares`；B3 组合降仓允许穿透底仓（`WOLF_SELL_BASE_EXEMPT` / `WOLF_SELL_EXEMPT_REDUCE` 已有），但必须显式开关。
**跌停/停牌**：`quote` 无价/跌停 → 不挂单，记录 `blocked`，次日重试。
**数据缺失**：`level=None` 或 `close=None` → 不下单，发 `data_warning`；**fail-closed 对卖出**（宁可不卖，不拿假数据卖）。

### 3.7 Agent 的角色（可选，Phase 7）

**只做“性质评估”，不做“价格/动作”**：

输入：`break_context.json`（levels、signals、wave、position、volume、相对强度、板块状态），不含原始全量行情。
输出：

```json
{
  "assessment": "true_break|false_break|unclear",
  "severity": "B0|B1|B2|B3|B4",
  "evidence": ["收盘 9.72 < 平台低 9.85", "量比 1.42", "板块同步破位"],
  "invalidation": "收盘站回 9.85 且量比 < 1.0",
  "action_tier": "alert|reduce_t|reduce_half|cap_50|cap_30|cap_0_30",
  "confidence": 0.0
}
```

**硬约束**：
- 不能创建/修改 `level.price`；只能用程序给的 levels。
- 不能把 B3 降级为 B0/B1（硬闸不可被 agent 关掉）；只能在 B2/B4 内选择允许的动作档。
- 不能直接下单；只能写 `break_agent_opinion`，由 `BreakStopService` 决定是否采纳。
- 必须能输出 `unclear`；没有证据不许编。
- 变更检测用 `state_key = date:symbol:level_type:severity`，措辞变化不推送。

**评估**：用 §1.4 的 10 个判例做盲测；如果 agent 与程序一致率 < 70%，或不能稳定报告矛盾 → **不上线**（保持纯程序）。

### 3.8 配置（新增，全部默认关/影子）

| 变量 | 默认 | 说明 |
|---|---|---|
| `WOLF_BREAK_STOP` | `0` | 总开关；0 = 完全不影响生产 |
| `WOLF_BREAK_MODE` | `shadow` | `shadow|alert|execute_index|execute_all` |
| `WOLF_BREAK_INDEX_SYMBOL` | `000001.SH` | 指数标的 |
| `WOLF_BREAK_INDEX_MIN_MA` | `2` | 至少破几根大级别 MA（沿用 `t_index_break`） |
| `WOLF_BREAK_INDEX_CONFIRM_DAYS` | `1` | 连续收盘确认 |
| `WOLF_BREAK_VOL_DIRECT` | `1.2` | 放量直接确认 |
| `WOLF_BREAK_VOL_SHRINK` | `0.8` | 缩量分支 |
| `WOLF_BREAK_VOL_STRONG` | `2.0` | 强放量 |
| `WOLF_BREAK_RECLAIM_DAYS` | `2` | 假破位/收回窗口 |
| `WOLF_BREAK_BUFFER` | `0.003` | 收盘破位缓冲 |
| `WOLF_BREAK_INDEX_CAP` | `0.50` | B3 默认目标仓位 |
| `WOLF_BREAK_INDEX_CAP_DEEP` | `0.30` | B3-2 深度破位 cap |
| `WOLF_BREAK_DOWN_CAP` | `0.30` | B3-3 down-wave 上限 |
| `WOLF_BREAK_DOWN_DEFAULT` | `0.0` | B3-3 无短线仓时的默认目标 |
| `WOLF_BREAK_ALLOW_ZERO` | `1` | down-wave 允许目标 0 |
| `WOLF_BREAK_STOCK_EXEC` | `0` | 个股 B2 是否自动减仓（默认只提示） |
| `WOLF_BREAK_STOCK_REDUCE` | `0.50` | 个股减仓比例 |
| `WOLF_BREAK_TIME_STOP_DAYS` | `13` | 时间止损窗口 |
| `WOLF_BREAK_SWING_PCT` | `3` | 波段低点 −3% |
| `WOLF_BREAK_TARGET_DRIVES_FLOOR` | `0` | 是否让 B3 target 驱动 `t_capacity` 底仓 floor |
| `WOLF_BREAK_AGENT` | `0` | agent 性质评估 |

### 3.9 DB / API / QQ

**表**（append-only，语义键幂等）：

```sql
break_events(
  id bigserial primary key,
  asof date not null,
  symbol text not null,
  domain text not null,          -- index|sector|stock
  level_type text not null,
  level_price numeric,
  phase text not null,           -- intraday|close
  status text not null,          -- watch|intraday_break|close_break|shrink_break|rebound_fail|confirmed|reclaimed|false
  close numeric, low numeric, vol_ratio numeric,
  wave_op text, position_pnl numeric,
  action text, target_pct numeric,
  reason text, mode text, data_warning text,
  created_at timestamptz default now(),
  unique(asof, symbol, level_type, status)
);

break_actions(
  id bigserial primary key,
  asof date not null, symbol text not null,
  action text not null, target_pct numeric,
  severity text, level_type text, reason text,
  status text default 'pending',   -- pending|executed|blocked|skipped
  trigger_id bigint, executed_at timestamptz,
  unique(asof, symbol, action)
);

break_agent_opinion(
  id bigserial primary key, asof date, symbol text,
  assessment text, severity text, action_tier text,
  evidence jsonb, invalidation text, confidence numeric,
  model text, created_at timestamptz default now()
);
```

**API**：
- `GET /api/v1/break/state?date=YYYYMMDD` → 最新 levels + status + actions。
- `GET /api/v1/break/events?symbol=&from=&to=` → 事件流。
- `POST /api/v1/break/replay`（`{date, mode}`）→ dry-run，不写 `break_actions`。
- `GET /api/v1/break/config` → 只读配置与开关。

**QQ/面板**：
- 14:40 预览：指数状态、持仓距破位最近 3 只、预计动作。
- 14:55 执行：B3 目标仓位/已减仓、B2 名单（若开启）。
- 15:05 收盘：确认破位、假破位、次日 invalidation。
- 只在 `state_key` 变化时推送；无变化静默。

---

## 4. 实施计划（12-14 个工作日）

| 阶段 | 内容 | 交付物 | 主要文件 | 验收 | 回滚 |
|---|---|---|---|---|---|
| **P0 冻结/盘点**（0.5d） | 记录现有六层行为与开关；跑一遍现有测试；建判例 fixture | `docs/break-baseline.md`、`break_cases.json` | `backend/tests/` | 现有测试全绿；判例日期/点位核对 | 无代码改动 |
| **P1 Level Registry**（2d） | 类型化 level：platform/trend/neckline/gap/MA/BOLL/prior_high/prior_low；PIT；语义键 | ✅ `backend/app/services/break_levels.py` + `backend/tests/test_break_levels.py` + `backend/tests/fixtures/break_cases.json`（2026-10-23 初版，16 tests 全绿） | 新文件 | 12 个判例 fixture（C1-C12）；指数判例 level 命中；无未来数据；数据不足返回 None | 纯新增，开关关 |
| **P2 Detector**（2d） | 状态机：intraday→close→shrink/rebound→confirmed/reclaimed；量比分支；前一日低点 3×15min 确认 | ✅ `backend/app/services/break_detector.py` + `backend/tests/test_break_detector.py`（2026-10-23 初版，15 tests 全绿） | 新文件 | C6/C9/C11/C12 状态正确；盘中不产生卖出；回放一致 | 纯新增 |
| **P3 Policy**（1.5d） | B0-B4、B3-1/2/3 三档、动作矩阵、仓位 cap、盈亏垫/段位修正、卖出顺序 | ✅ `backend/app/services/break_policy.py` + `backend/tests/test_break_policy.py`（2026-10-23 初版，23 tests 全绿） | 新文件 | C1/C2/C3/C5/C7/C10 动作 tier/target 正确；down-wave 默认 0、30% 仅作 cap | 纯新增 |
| **P4 Orchestrator（非 shadow）**（2d） | 14:50 编排；P1→P2→P3；写 `break_events`/`break_actions`；QQ；API；`jobs/break_stop_run.py` | ✅ `break_stop_service.py`、`break_repository.py`、`break_orm.py`、`api/break_stop.py`、`jobs/break_stop_run.py`（2026-10-23 初版，4 tests + 58 全链路 tests） | 新文件 + `database.py` migration + `main.py` 注册 + `config/tasks.yaml`（task disabled） | alert 真实落库/推送；0 下单；重复跑幂等；数据缺失有 warning | `WOLF_BREAK_STOP=0` / 任务 disabled |
| **P4.1 回测前** | 不开 `execute_index`；不开 task；先用 API 手动跑 `POST /api/v1/break/run` 看 2-3 天 | 手动验证记录 | — | 事件数/推送内容/动作符合预期 | 随时关 |
| **P5 回测/验收**（2d） | `jobs/eval_break_stop.py`：P5a 指数硬闸已跑（hold / MA60 / MA60+144 / B3 recover / B3 norecover / B3-major2 两臂） | ✅ 报告 `docs/break-stop-backtest.md`（2026-10-23） | `jobs/eval_break_stop.py` | **P5a 结论：B3 压左尾但收益让 11pp，MA60 反而更优；不支持开启 `execute_index`** | 只跑离线 |
| **P6 指数硬闸开启**（1d + 观察） | 仅 `execute_index`：B3 目标仓位；B2/B4 仍 alert；用户批准后开 | 生产配置 + 监控 | `.env`、`t_capacity` | 影子→小仓验证；无重复卖；回撤/仓位符合目标 | `WOLF_BREAK_MODE=shadow` |
| **P7 Agent（可选）**（1.5d） | 性质评估；只写 opinion；盲测 10 判例 | `break_agent.py` + 测试 | 新文件 | 一致率 ≥70%；能报“看不清”；误报可审计 | `WOLF_BREAK_AGENT=0` |
| **P8 清理**（0.5d） | 标记/删除重复的 `_check_break_low_stop` 百分比止损；统一文档 | 清理 commit | `stop_loss_monitor.py` | 无行为变化；测试全绿 | revert |

**顺序理由**：先有 level（事实），再有状态机（确认），再有动作（决策），最后才接线；否则会重复“先写提示词/先写规则、再补数据”的错误。

---

## 5. 验收标准

### 5.1 判例（必须全部过）

- C1-C10 的 level 命中：价格误差 ≤ 0.5%，动作 tier 完全一致，target_pct 误差 ≤ 0.05。
- C6 缩量分支：必须出现 `shrink_break → rebound_fail → confirmed`，不能直接 confirmed。
- C7 时间止损：第 13 个交易日收盘触发，不能提前/延后。
- C10 3764：有效跌破 → B3；站回 → reclaimed。
- 盘中案例：所有 B0 均无卖单。

### 5.2 回测（n≥400 腿，H1/H2 分段，块状 t）

| 臂 | 期望 |
|---|---|
| C_hold（不止损） | 基线：均值最高、左尾最差 |
| B_idx60（现有 audit） | 均值 +1.29%、最差 5 笔 −16.8% |
| 现状六层 | 均值约 −0.22%（audit 近似） |
| 新：指数结构硬闸 | 左尾 ≤ B_idx60；均值不低于 B_idx60 −0.5pp |
| 新：指数 + 个股提示 | 与“指数硬闸”同收益，但事件更多（只提示） |
| 新：指数 + 个股减仓 | 只有在该臂同时改善左尾且均值不低 >0.5pp 时，才允许 `execute_all` |

**必报指标**：均值/中位、胜率、p5、最差 5 笔、最大回撤、止损后 10 日反弹、止损正确率、换手/成本。
**禁止**：只报胜率；不做同池同日基线；不做 H1/H2 分段；n<100 当结论。

### 5.3 影子

- 至少 20 个交易日、0 下单、0 重复事件。
- 每日事件数：指数 0-3 条、个股 0-10 条（超过说明 level 太密/阈值太松）。
- 数据缺失率 < 2%，且每次缺失都有 `data_warning`。
- 14:55 计算延迟 < 5s；失败非零退出 + 告警（不输出“看起来正常”的过期报告）。

### 5.4 工程不变量

- `WOLF_BREAK_STOP=0` → 生产行为逐位不变。
- 同一 `(asof, symbol, action)` 只执行一次；回放重复跑结果一致。
- 所有事件有 `asof`、`mode`、`source`；回放 `where asof <= 回测日`。
- 不硬编码 API key；不在容器里跑大 pandas（512MB）。
- 新增单测全绿；现有 `test_stop_*`、`test_wolf_*` 不回归。

---

## 6. 风险与红线

| 风险 | 后果 | 对策 |
|---|---|---|
| **个股破位止损负贡献** | 卖在半路（audit：正确率 46%，10 日反弹 +6.72%） | 个股层默认 alert；只有验证后才减仓；B3 才硬执行 |
| **段位误用** | 在 4-3 支撑位把破位当卖点，错过买点 | 段位/位置前置；build 段 B2 只 alert；C10 vs 2026-08-03 判例对照 |
| **盘中假破位** | 插针卖在最低 | 盘中只 B0；收盘确认 + buffer + 收回窗口 |
| **缩量破位** | 洗盘被卖 | shrink 分支等反抽不过颈线；地量不割 |
| **指数阈值敏感** | MA20/MA60 结论摆动（audit −0.40 ~ +1.29） | 默认 MA60/144/200；回测多阈值；不把 MA20 当硬闸 |
| **重复卖** | 指数 B3 + 个股 B2 同一天卖两次 | 单一责任人；`(asof,symbol,action)` 幂等；B3 组合降仓优先于个股 |
| **底仓被啃光** | 反复减半 | 每票每轮最多 N 笔；`take_penetrate_quota`；B3 目标仓位而非逐只清 |
| **数据过期/口径混用** | 拿昨日数据卖今日 | `mode/asof`；数据缺失 fail-closed；指数点 vs ETF 价标注单位 |
| **agent 自证** | 自己写 level 自己读 | 程序判资格；agent 只评估；人保留否决 |
| **过度拟合** | 10 个判例写死 | 留出日（2026-06-30 等）+ 换品种（2022-04-27）+ 失败样例 |
| **上线即清仓** | 存量持仓“早就破位”被一次性卖 | `FRESH_ONLY` 护栏：近 N 日从未站上该线 → 只 notice（沿用 `WOLF_TREND_FRESH_ONLY` 思路） |

**红线**：
1. 不自动卖个股，除非用户明确批准 `execute_all`。
2. 不用盘中破位当收盘破位。
3. 不用未来数据（次日 reclaim 不参与当日动作）。
4. 不在 `stop_loss_monitor` 新增卖出逻辑。
5. 不改 `WOLF_TREND_STOP` / `WOLF_EARLY_STOP` 的生产默认值。
6. 不在生产容器跑重回测。
7. 不把“他这么说”当“这么做有效”；语料给假设，数据给结论。

---

## 7. 与现有计划的关系

| 文档 | 关系 |
|---|---|
| `docs/stop-layer-audit.md` | 本方案的量化基线；选项①（个股降级、指数唯一硬止损）与本方案一致 |
| `docs/plan-exit-alignment.md` §5 阶段 3（R8） | 本方案是 R8 的具体实现；不重复阶段 0-2 的做 T/兑现工作 |
| `docs/wolf-trading-playbook-taxonomy.md` X3 | 本方案是 X3 的工程化 |
| `docs/ai-agent-lessons-from-b-rebound.md` | 本方案的 §3.7、§5.4、§6 直接继承其反面清单 |
| `docs/wolf-daily-log-*` | §1 判据的逐日原文来源；新增判据走 dsh 精读，不用正则 |

---

## 8. 决策状态（已拍板，2026-10-23）

| # | 问题 | 决定 |
|---|---|---|
| 1 | 硬闸范围 | **指数级 = 唯一自动止损；个股级默认只提示** |
| 2 | 指数口径 | **只用上证 000001.SH** |
| 3 | B3 目标仓位 | **默认 50%、深度 30%（cap）；down-wave 0-30% cap，默认 0** |
| 4 | 个股自动层 | **先回测；`WOLF_BREAK_STOCK_EXEC=0` 不变** |
| 5 | Agent 层 | **做完规则再评估；P7 暂缓** |

**因此 v1 的交付范围 = P0-P6（不含 P7 agent）**；P5 回测是 P6 开启前的硬门槛。

### P5a 结果（2026-10-23，指数硬闸）

| 臂 | 总收益% | 最大回撤% | Sharpe | 平均仓位% |
|---|---:|---:|---:|---:|
| hold | 28.53 | −11.28 | 1.45 | 100 |
| MA60 | 20.37 | −9.57 | 1.32 | 83.8 |
| B3 recover | 17.14 | −8.98 | 1.18 | 75.5 |
| B3 norecover | 9.80 | −5.22 | 1.25 | 41.0 |

**结论**：B3 能压左尾，但代价约 11pp 收益；在这段样本上 MA60 更优。**P5a 不支持开启 `execute_index`**；下一步 P5b 把个股 B2、as-of 浪型、点位表和恢复状态机纳入后再判。

---

## 附录 A：文件清单

**新增**
- `backend/app/services/break_levels.py` ✅（P1，2026-10-23）
- `backend/app/services/break_detector.py` ✅（P2，2026-10-23）
- `backend/app/services/break_policy.py` ✅（P3，2026-10-23）
- `backend/app/services/break_repository.py` ✅（P4，2026-10-23）
- `backend/app/services/break_stop_service.py` ✅（P4，2026-10-23）
- `backend/app/models/break_orm.py` ✅（P4，2026-10-23）
- `backend/app/api/break_stop.py` ✅（P4，2026-10-23）
- `jobs/break_stop_run.py` ✅（P4，2026-10-23）
- `backend/tests/fixtures/break_cases.json` ✅（P1，12 个判例）
- `backend/tests/test_break_levels.py` ✅（P1）
- `backend/tests/test_break_detector.py` ✅（P2）
- `backend/tests/test_break_policy.py` ✅（P3）
- `backend/tests/test_break_stop_service.py` ✅（P4）
- `backend/app/services/break_agent.py`（P7，可选）
- `backend/tests/test_break_cases.py`（P5 判例回归）
- `backend/tests/test_break_integration.py`（P6 接线）
- `jobs/eval_break_stop.py`（P5）
- `docs/break-stop-backtest.md`（P5）
- `docs/break-baseline.md`（P0）

**已修改**
- `backend/app/database.py`：`break_events` / `break_actions` 迁移 + model import。
- `backend/app/main.py`：注册 `break_stop` router。
- `config/tasks.yaml`：新增 `break_stop_1450`（**enabled: false**，P5 回测后再开；已 `safe_load` 校验）。

**待 P5/P6 修改（尚未动）**
- `backend/app/services/t_monitor.py`：新增 `_check_break_stop()`（读 `break_actions` 执行）；不重写现有止损。
- `backend/app/services/t_capacity.py`：可选让 `break_target` 驱动 floor（开关默认 0）。
- `backend/app/services/daily_decision.py`：L6 增加破位状态/动作摘要。
- `backend/app/services/stop_loss_monitor.py`：标记 `_check_break_low_stop` 为 legacy，不再新增卖出。

**复用不重写**
- `wolf_early_stop.py`、`wolf_trend_stop.py`、`wolf_passive_stop.py`、`t_stop_loss_guard.py`、`t_index_break.py`、`support_resistance.py`、`t_index_break.py`、`t_playbook.py`、`t_gateway.py`。

---

## 附录 B：一句话交接

**先把“什么算破位”做成可回放的事实（levels + 状态机），再把“破位后做什么”做成纯函数矩阵（B0-B4 × 段位 × 盈亏垫），最后只让指数级硬闸自动执行；个股级先提示、再验证、再决定。**
