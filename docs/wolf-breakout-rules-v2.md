# 突破确认规则 v2（A 原话 + B 最小代理 + Agent 判收盘强弱）

> 2026-10-30 更新：C 类自设全部移除；B6 的 0.5 中点删除，收盘强弱改由 agent 判；B4/B5 数据已补。
> 数据层：`apps/main_line/breakout_context.py`（主题代理 ETF、银行/黄金 ETF、收盘强弱事实底稿、agent payload）。
> 判例：`docs/wolf-breakout-proxy-case-audit.md`。

---

## 1. A 类：他的话，直接执行

| # | 规则 | 原话/日期 |
|---|---|---|
| A1 | 段位：只在 build/side 做新突破；defense/exit 不做 | 2026-01-17；4-5 不接 |
| A2 | 市场量能：攻关键位 ≥3WE；2WE 是地量；<2WE 不做突破 | 2026-09-03；2026-08-20 |
| A3 | 突破确认：收盘站上关键位/颈线/前高；连续 3 日收盘不破；盘中 3 个 15 分钟站上中轨 | 2025-12-19；2026-01-12；2025-08-14 |
| A4 | 买入窗口：9:45-10:00、14:00-14:30；高开不追、冲高不追 | 2025-04-15；2026-09-03 |
| A5 | 加仓：第一根加一半；第二根红 K 尾盘打满；回踩确认加仓（独立一次）；向下破线止损；单票每日 2 买 2 卖 | 2026-06-15；2022-06-08；2025-02-07 |
| A6 | 时间止损：13 日不创新高就撤 | 2025-08-18；2026-03-05 |
| A7 | 选板块：近一周 ≥2/3 天数在 10 日量能上；无 5 日连续净流出 | 2025-06-16 |
| A8 | 仓位：三方向、6 票；50/30/20 或 75/50/30 | 2025-08-11；2026-03-30；2026-04-14；2026-01-17 |
| A9 | 目标/止盈：0.382/0.618、前高、整数关；放量滞涨减；跌破平台/趋势线被动止盈 | 2026-03-05；2025-04-23；2026-01-29 |
| A10 | 均线：13/34/60/144；60 是底线，144 是最后底线 | 2021-01-28；2026-03-20 |
| A11 | 突破形态：放量一口吃完上方挂单；左侧=放量过压力，右侧=站稳 3 天/回踩 | 2026-02-12；2025-12-19；2025-10-27 |

---

## 2. B 类：最小代理（已过判例）

| # | 代理 | 最小可执行形式 | 判例依据 |
|---|---|---|---|
| B1 | 放量 | 盘中：最近 3 根 15 分钟量 > 前 3 根；或当日累计量/前一日同刻 >1。日线：当日成交额 > 前一日 | 10/27 1.18×、10/29 1.06×、2/25 1.12×+13:30 加速 1.82×，全部通过；不用 z≥1.5 |
| B2 | 站稳 | 连续 3 日收盘 ≥ 突破位（精确位） | 10/30 3986.9、10/31 3954.8 低于 4006，他自己说“本来就没有”；不用 ×0.97 |
| B3 | 滞涨 | 高位 ∧ 放量 ∧（**Agent 判弱收盘** ∨ 未创新高） | 6/13 收最低 20.7%、高点 09:45，agent 判 weak；1/12、10/29 收在最高附近，judge strong |
| B4 | 主线响应 | 主题代理 ETF 当日平均收益 >0 → `mainline_ok=true` | 1/12 +1.04%、2/25 +2.0%、4/30 +4.3%、10/29 +0.13% 通过；9/3 -0.93% 拒绝 |
| B5 | 避险抢戏 | max(银行, 黄金) 当日收益 > 主线收益 ∧ 主线收益 ≤0 → `hedge_flag=true` | 9/3 黄金 +2.28% > 主线 -0.93% → 拒绝；1/12/10/29/2/25 主线为正 → 不误杀 |
| B7 | 上影 | 上影 > 实体（可观测，供 agent 参考） | 6/13 上影 13.15%、实体 66.15%；4/30 上影 37.1% |
| B8 | 回踩三态 | 低点 ≤ 突破位后：收盘 ≥ 位=不破（加/持有）；收盘 < 位=观察；次日收盘 < 位=跌破确认（不买/减）；次日收盘 ≥ 位=假跌破（重新买） | 117 个历史事件：不破 87（fwd5 中位 +0.51%）、跌破确认 21（-1.04%）、假跌破 9（+1.16%）；见 `docs/breakout-retest-cases.md` |
| B9 | 指数确认 | 指数收盘站上关键位 + 3 日不破 | 10/29 后 10/30/10/31 低于 4006 → 不成立；1/12 通过 |
| B10 | 市场量能 | 2WE/3WE（A2 的数值） | 9/3 1.78WE → 拒绝；4/30 2.76WE <3WE → 拒绝 |
| B11 | 拥挤 | 公募拥挤方向（categorical） | 2025-11-12、2026-07-28 |
| B12 | 流动性/板块 | 工程过滤，不算策略因子 | 他的“小票不好判断”是定性 |

> **B6（0.5 中点）已删除**：收盘强弱不设数值阈值，交给 agent。

---

## 3. Agent 判收盘强弱

### 3.1 输入（只给事实，不给结论）

`apps/main_line/breakout_context.py::agent_payload(code, day, theme)` 产出：

```
task: judge_close_strength
symbol / as_of / mode(postclose)
facts:
  open/high/low/close/range
  close_pos_raw              # (close-low)/(high-low)，原始数字
  upper_shadow_pct           # 上影占振幅 %
  lower_shadow_pct
  body_pct
  close_vs_ma5_pct / close_vs_ma10_pct
  intraday:                  # 指数 5m（本地 index_5m.db）
    last_30m_ret_pct
    last_60m_ret_pct
    day_high_time
    morning_high / afternoon_high
    volume_last_30m_share
context: B4/B5（主线代理收益、银行/黄金收益、mainline_ok、hedge_flag）
allowed_labels: strong / weak / neutral / no_confident
rules:
  只依据 facts 数字，不引入外部记忆
  至少给 2 个数字证据
  允许 no_confident，不确定不要硬判
  不输出概率/评分/阈值触发
output_schema:
  close_strength: strong|weak|neutral|no_confident
  evidence: [含数字的字符串]
  invalidations: [可观测的反证条件]
```

### 3.2 判例事实（agent 应能自己读出强弱）

| 日期 | close_pos | 上影% | 日高时间 | 最后30m | 事实读法 |
|---|---|---|---|---|---|
| 2025-06-13 | 0.207 | 13.15 | 09:45 | -0.08% | 高点在早盘、收盘接近最低 → weak |
| 2026-01-12 | 0.937 | 6.33 | 14:30 | -0.06% | 收在高位、上影小 → strong |
| 2025-10-29 | 1.000 | 0.00 | 14:55 | +0.20% | 收在最高 → strong（但 3 日站稳仍需 B2） |
| 2026-04-30 | 0.629 | 37.10 | 14:40 | -0.03% | 上影大、收盘中等 → neutral/weak |
| 2026-09-03 | 0.309 | 40.68 | 09:50 | -0.22% | 高点早盘、上影大、收盘低 → weak |

> 关键：agent 只判“当天收盘强弱”；**3 日站稳、3WE、B4/B5 仍是硬门**，agent 不能覆盖。

### 3.3 程序消费方式

- `B3`：`高位 ∧ 放量 ∧ agent_label == "weak"` → 不追/减。
- `agent_label == "strong"` 不放宽 B2/B9/B10；只用于 B3 的滞涨判断。
- `no_confident` → 不做 B3 的加分/减分，按“未创新高”兜底。
- 所有 agent 输出落盘 `breakout_agent.jsonl`，带 `evidence`，便于事后审计。

---

## 4. B4/B5 数据层

### 4.1 主题 → 代理 ETF（数据适配，不是阈值）

| 主题 | 代理 ETF |
|---|---|
| AI/算力/科技、半导体/芯片 | `sh512480`、`sh588170` |
| 新能源/电池 | `sh515030`、`sz159755` |
| 军工/航天 | `sh512660` |
| 资源/周期 | `sh510410`、`sh518880` |
| 金融 | `sh512800`、`sh512000` |
| 医药 | `sh512010` |
| 消费/内需 | `sz159928` |
| 机器人/智能制造 | `sz562500` |
| 电力/公用 | `sh561560` |
| 汽车/智驾 | `sh516110` |
| 传媒/游戏 | `sh512980` |
| 农业 | `sz159825` |
| 稳增长/基建 | `sh516950` |

避险：银行 `sh512800`，黄金 `sh518880`。

### 4.2 计算

```
mainline_ret = mean(主题代理 ETF 当日收益)
mainline_ok  = mainline_ret > 0
hedge_best   = max(bank_ret, gold_ret)
hedge_flag   = hedge_best > mainline_ret and mainline_ret <= 0
```

- 无阈值；只判方向和相对强弱。
- 数据缺失 → `None`，fail-closed。
- 缓存：`data/breakout_etf_cache.json`。

### 4.3 判例结果

| 日期 | 主线 | mainline_ret | 银行 | 黄金 | B4 | B5 |
|---|---|---|---|---|---|---|
| 2025-10-29 | 半导体/芯片 | +0.13% | -1.93% | +1.11% | ✅ | 不触发 |
| 2026-01-12 | AI/算力/科技 | +1.04% | +0.50% | +1.93% | ✅ | 不触发 |
| 2026-02-25 | 新能源/电池 | +2.00% | -0.39% | +0.08% | ✅ | 不触发 |
| 2026-04-30 | 半导体/芯片 | +4.30% | -0.62% | +0.43% | ✅ | 不触发（由 3WE 拒绝） |
| 2026-09-03 | AI/算力/科技 | -0.93% | -0.47% | +2.28% | ❌ | **触发**（拒绝） |

> 4/30 的诱多不是“主线弱”，而是“指数在 4100 关口、市场额只有 2.76WE <3WE”；由 B10 拒绝，不是 B4/B5。9/3 是“主线弱、黄金强”，B4/B5 直接拒绝。

---

## 5. C 类：已移除

| 旧规则 | 旧值 | 移除原因 |
|---|---|---|
| 突破窗口 | N=20 | 他全语料没有“20 日高点”；10/29 的突破位是 4006 |
| 突破幅度 | 1.005 | 10/29 +0.49% 被 0.5% 缓冲误杀 |
| 量能 | z≥1.5 | 10/27/10/29/2/25 不满足，但他认 |
| 站稳 | ×0.97 | 10/30/10/31 低于 4006，会误判确认 |
| 高位 | 0.8 分位 | 1/12、10/29 高位有效 |
| 追高 | 3% | 他只有“不追高/高开不追” |
| 趋势监控 | 1.5×均量+MA20+TOP-N | 项目自设 |
| 关键位容差 | 1.5% | 自设 |
| 日亏/单笔 | 2%/5% | 自设 |
| 试仓/加满 | 1/3、2/3 | 他说“一半/打满” |
| 每方向腿数 | 3 | 他说“三个方向/6 个票” |
| 收盘位置 | 0.5 中点 | **已删除，交给 agent 判** |

---

## 6. 执行流程

```
1) 段位：build/side？否则只观察
2) 市场：≥2WE？攻关键位 ≥3WE？否则只做 T
3) 选板块：≥2/3 天数在 10 日量能上？无 5 日连续流出？
4) level：关键位/颈线/前高/平台
5) 突破：收盘 > level（无缓冲）
6) 放量：3×15 加速，或当日额 > 前一日
7) 确认：连续 3 日收盘 ≥ level；3×15 站上中轨
8) 过滤：B4 主线响应？B5 避险不抢戏？Agent 判非弱收盘/未创新高？
9) 执行：9:45-10:00 / 14:00-14:30；高开不追；第一根一半，第二根红 K 打满
10) 持有/加仓：回踩不破 level 加一次；跌破 level/破线止损；13 日不创新高撤
```

---

## 7. 仍需补的

- **B1 盘中**：ETF/个股 5m 数据（本地只有指数 5m）。
- **B8 回踩**：已补 117 个判例（见 `docs/breakout-retest-cases.md`）；下一步接影子。
- **B11 拥挤**：历史 as-of 的 `rotation_crowding`。
- **Agent 稳定性**：同一份 facts 多次调用要一致；用温度 0 + 结构化输出 + 盲测。

---

## 8. 生产接线（2026-10-30）

| 位置 | 接线方式 | 开关/日志 |
|---|---|---|
| `backend/app/services/p2_entry_gate.py` | `p2_gate_check` 内加 B4/B5：B5→`hard_block`（`WOLF_B4B5_HARD=1`）或降级 0.5；B4 false→降级 0.5；unknown→fail-open | `WOLF_B4B5_GATE=1`、`WOLF_B4B5_HARD=0/1`；`p2_gate_log.jsonl` 带 `b4b5` |
| `apps/main_line/switch_builder.py` | `b4b5_filter(buy_new)`：主题 `action ∈ {block,downgrade}` → 剔掉该主题新买腿；不影响持仓/埋伏腿 | `WOLF_B4B5_GATE=1`；日志 `[switch_builder] B4/B5 剔除...` |
| `apps/main_line/stock_confirm_judge.py` | 每个主题算一次 `gate(mt, prev_trade_day())`，把 `mainline_ok/hedge_flag` 以 0/1 传给 `confirm_chain` 的 F3/F4；B4 false 或 B5 true → 该主题个股 stage 变“证伪” | `WOLF_B4B5_ENABLED=1` |
| `backend/app/services/trade_graph.py` | `_read_stock_confirm_context` 末尾追加 B4/B5 段（主线收益/银行/黄金/action），供 LLM 上下文 | 失败静默 |
| `jobs/market_scan.py` | 盘中扫描上下文追加 B4/B5 一行 | 失败静默 |

**PIT**：生产门用 `prev_trade_day()`（上一交易日收盘），盘前/盘中不偷看当日收盘。判例统计（`docs/breakout-retest-cases.md`）是同日事后读法，两者不要混。

**回滚**：`WOLF_B4B5_GATE=0` 关闭 p2/switch 门；`WOLF_B4B5_ENABLED=0` 关闭 stock_confirm 的 F3/F4；`WOLF_B4B5_HARD=0` 让 p2 的 B5 只降级不硬拦。

**测试**：`backend/tests/test_b4b5_gate.py`（gate/block/downgrade/switch filter/F3F4）、`backend/tests/test_breakout_context.py`（B4/B5/agent/retest_state）。

---

## 9. B8 生产接线（2026-10-30）

| 位置 | 行为 | 开关 |
|---|---|---|
| `backend/app/services/p2_entry_gate.py` | `_b8_gate`：`break_confirmed` → 硬拦（`WOLF_B8_HARD=1`）或降级 0.5；`break_pending` → 降级 0.5；`hold`/`false_break`/`no_setup`/`no_touch` → pass；unknown → fail-open | `WOLF_B8_GATE=1`、`WOLF_B8_HARD=1` |
| `apps/main_line/switch_builder.py` | `b8_filter(buy_new)`：最近突破后 `block/downgrade` 的票不布新买腿；不破/假跌破/未回踩放行 | `WOLF_B8_GATE=1` |
| `backend/app/services/trade_graph.py` | 上下文追加“B8 主线ETF(512480)”状态 | 失败静默 |
| `jobs/market_scan.py` | 盘中上下文追加 B8 一行 | 失败静默 |

**PIT**：B8 用 `prev_trade_day()`（上一交易日收盘），只认已经发生的回踩/次日确认；盘中不拿当日收盘当确认。

**实现**：`breakout_context.b8_state_from_bars`（纯函数）/ `b8_state`（取日线）/ `b8_gate`（三态门）；`retest_state` 为底层状态函数。

**回滚**：`WOLF_B8_GATE=0` 关闭 p2/switch；`WOLF_B8_HARD=0` 让 `break_confirmed` 只降级不硬拦。

**测试**：`test_breakout_context.py` 增加 hold/break_confirmed/false_break/`b8_gate`；`test_b4b5_gate.py` 增加 p2 B8 硬拦、switch B8 过滤。共 16 passed。

---

## 10. 共享买入前门 pre_trade_gate（2026-10-30）

**唯一入口**：`backend/app/services/pre_trade_gate.py::pre_trade_gate(symbol, intent="new_base", ts_code=None, source="api")`

组成：`p2_gate_check`（wave/systemic/macro + B4/B5 + B8）。只对买入意图生效；卖出/减仓/止损直接放行。

```python
g = pre_trade_gate("SZ300304", intent="probe", source="stock_agent_position")
# {"allow": bool, "hard_block": bool, "multiplier": float, "reasons": [...],
#  "b4b5": {...}, "b8": {...}, "wave": ..., "macro": ..., "systemic": ...}
```

- 买入意图：`new_base/add_base/refill_base/t_refill/probe/add/build/open/t_buy/buy` → 过门。
- 卖出意图：`sell/reduce/exit/stop/close/stop_loss/take_profit` → 直接 allow。
- `allow=false`：不买；`multiplier<1`：降级（调用方按 low/probe 处理）。
- 日志：`data/pre_trade_gate.jsonl`。

**API**：`POST /api/v1/indicator/pre-trade-gate`，body `{symbol, intent, ts_code?, source?}`。

**已接线**：

| 路径 | 状态 |
|---|---|
| `jobs/stock_agent_position.py` | ✅ 选股后、`calc_position` 前调用；blocked 跳过，multiplier<1 降级 low/probe |
| `jobs/theme_sleeve_trade.py` | ✅ 同上 |
| `check_entry_filters` | ✅ 内部已含 `p2_gate_check`（B4/B5/B8） |
| `auto_trade`（pi_trade） | ⚠️ 软约束：提示词要求买入前过 `check_entry_filters`；未强插 pre_trade_gate |
| `switch_builder` / `stock_confirm_judge` | ⚠️ 代码已接，但 `tranche_ladder_report` / `stock_confirm_refresh` 当前不在 `tasks.yaml` |

---

## 11. 下单边界硬门（2026-10-30）

在 `backend/app/api/trades.py::execute_trade` 的 **buy 侧**、`executor.buy` 之前加共享门：

```python
_gate_ok, _gate_why, _gate_info = _pretrade_gate_for_trade(trade)
if not _gate_ok:
    return TradeResponse(status="rejected", reason="pre_trade_gate: " + _gate_why, ...)
```

行为：

| 条件 | 动作 |
|---|---|
| `side != buy` | 放行（不拦卖出/止损） |
| `intent in {t_buy, t_refill, t_add}` | 放行（T 回补走 t_gateway 独立纪律，不套 B8） |
| `WOLF_PRETRADE_ORDER_GATE=off` | 放行 |
| `shadow` | 放行，但日志写 `SHADOW would-block` |
| `enforce`（默认） | `allow=false` → 拒单；`multiplier<1` → 放行 + 降级日志 |

覆盖：`auto_trade` 的 agent 下单、`stock_agent_position`、`theme_sleeve_trade`、手动 `/trades`。

未覆盖：直接调 `executor.buy()` 的路径（`t_gateway`/`t_build`/`wolf_253`/池监控）。它们不走 `/trades`；如果也要拦，需在 `marcus_trade.check_risk` 加 `intent` 参数并只对 `new_base/probe/add_base` 生效。

**与卖出侧对称**：卖出侧已有 `marcus_trade.check_risk` 的 `_check_sell_trend_constraint`（MA5>MA20 + 背离<3 → 阻止手动卖出，止损豁免）；本次买侧门放在 `/trades` 接口边界，正好是“agent → 交易接口”之间的位置。
