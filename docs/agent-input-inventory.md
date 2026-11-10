# 交易 agent 的输入清单（Pi / auto_trade）

> 方法：静态读 `backend/app/services/trade_graph.py` 的 `node_fetch_context`（1447-1488）与各 `_read_*_context` 生成函数；
> **未调用 `run_trade_decision`、未下单、未改任何逻辑**。目的是回答一个问题：狼大那句话背后的依据，我们到底喂给了 agent 没有。

## 一、实际注入的 context（20 项 + 3 项已关闭）

| # | context | 生成函数 | 数据源 | 里面是什么 | 对应狼大哪一环 |
|---|---|---|---|---|---|
| 1 | scan_report_text | `_read_scan_report` | `memory/market-scan-logs/*.jsonl` | 盘中扫描报告（立场/热点/仓位） | 盘面 |
| 2 | portfolio_json | `_read_portfolio` | PG 持仓/账户 | 持仓、成本、现金 | 自身仓位 |
| 3 | stance_context | `_read_stance_context` | StrategyChain | 上一轮 stance/仓位上限 | 自身连续性 |
| 4 | trade_mode_instruction | `_get_trade_instruction` | 窗口 + regime | 9:50/14:20 时段指令 | 时间窗（弱） |
| 5 | main_line_context | `_read_main_line_context` | `data/mainline/<date>/stock_agent_selected.json` | L0 主线 + P3 selected/probe | 主线/方向 |
| 6 | position_context | `_read_position_context` | `position_class_result.json` | 概念高低位、减仓/低吸清单 | 位置 |
| 7 | cycle_context | `_read_cycle_context` | position_state | 持仓周期、减仓预算 | 仓位纪律 |
| 8 | position_levels_context | `_read_position_levels_context` | PG `position_levels` | 每只持仓 down/up | 点位 |
| 9 | confirm_context | `_read_confirm_context` | `position_class_result.json` | LOW 确认链、拥挤过滤 | 位置+拥挤 |
| 10 | stock_confirm_context | `_read_stock_confirm_context` | `stock_confirm_result.json` | 主线成分股确认比例 | 广度 |
| 11 | wave_context | `_read_wave_context` | **`data/wave_state.json`（已删）** | 浪型 level/sub/operation/gate | **级别/牛熊** |
| 12 | three_tier_context | `_read_three_tier_context` | `_wave_level_gate()` + `p3_position_tiers.json` | 三仓档位可做/禁做 | 仓位档位 |
| 13 | rotation_gate_context | `_read_rotation_gate_context` | `rotation_gate` + wave op | 能否切低/只做主线内轮动 | 轮动 |
| 14 | rotation_switch_context | `_read_rotation_switch_context` | `data/rotation_switch_plan.json` | 切谁/买哪条链 | 轮动 |
| 15 | macro_context | `_read_macro_context` | `macro_state.json` | GJD/两融/北向 + 四类开关 | 资金主体/宏观 |
| 16 | risk_context | `_read_risk_context` | PG `risk_flags` | block/review/reduce 名单 | 风控 |
| 17 | discipline_context | `_read_discipline_context` | wolf_discipline | 周末降仓、板上减半 | 纪律 |
| 18 | plan_context | `_read_plan_context` | 计划库 | 命中计划（高优先级） | 计划 |
| 19 | wolf_t_context | `_read_wolf_t_context` | wolf_t_rules | 做T纪律 | 做T |
| 20 | wolf_judge_context | `_read_wolf_judge_context` | wolf_judge 六因子 | 要不要出手（软指导） | 出手闸（弱） |
| — | pool_context | — | **已关闭 2026-09-09** | 候选池（非狼大体系） | — |
| — | regime_context | — | **已关闭 2026-09-03** | 震荡/趋势市场状态 | — |
| — | style_context | — | **已关闭 2026-09-03** | 进攻/防御/资源避险 | — |

## 二、四个结构性发现

### 1. 最像他的那个对象，根本没喂进去

`b_rebound_daily`（PG）里有：`bottom_state / cond_feet / cond_volume / cond_ma5 / cond_count / intraday_turn / volume_phase / verdict / position_pct / position_reason / position_delta / episode_days`——正好是 11-04 那笔的三个条件 + 仓位建议。

**`grep -rn 'b_rebound' backend/app/services` = 0 命中。** 它只被 `api/wave.py` 读出来给前端看，交易 agent 从来没见过。

### 2. 三套市场状态互相打架

| 状态源 | 谁在用 | 现状 |
|---|---|---|
| `wave_state`（d1–d5/down） | wave_context、三仓档位、rotation_gate | **唯一被注入** |
| `market_diagnosis`（🟡 震荡市） | — | `trade_graph.py:488` 明令「一律忽略，属废弃文案」 |
| `t_regime_state`（ACTIVE/CAUTIOUS） | 做T gate | 只管 T，不进主线决策 |

所以 agent 看到的“市场状态”只有浪型档位，而浪型档位在 11/08 之后又读不到文件（见下）。

### 3. 结论写在证据前面

- PG `T_TRADE_SYSTEM_PROMPT` 第一条：「mainline_active=false、tradeable=false、浪型 defense/exit：不新开底仓，只做T/减仓/观察」。
- `trade_graph.py:1613` 内联 prompt 又写一遍：「主线不存在、mainline_active=false、tradeable=false 时，不新开底仓」。
- `SAFETY_GATE_BYPASS = True`（`trade_graph.py:65`）：确定性硬门关闭，所以这两句话就是唯一约束。

**20 个 context 喂进去，结论在第一行就写死了。** 这是 11/04「不做」的真正来源。

### 4. 还有三个 context 在读已删除的文件

| 位置 | 读什么 | 11/08 之后的实际行为 |
|---|---|---|
| `_wave_level_gate`（1387，读 1393） | `data/wave_state.json` | 文件不存在 → `operation=side, gate=normal`（静默放行） |
| `_read_wave_context`（1257） | 同上 | 回退 rule-based 结构判定，档位与 PG 不一致 |
| `_read_three_tier_context`（1299）→ `_wave_level_gate` | 同上 | 按 **side** 算档位上限，比真实的 defense 更宽松 |

这三处不在上次 `state_io` 收口范围内（它们读的是变量 path，linter 的 `open(...wave_state)` 正则抓不到）。

## 三、对照狼大的 8 环：他有 / 我们有 / 缺

| 环 | 他的依据 | 我们的数据 | 是否进 agent | 判定 |
|---|---|---|---|---|
| 级别/牛熊 | 3674 牛熊线、月线 | `wave_state`（PG，down·下跌1浪） | ❌ 读文件失败，回退 rule-based | **半缺** |
| 时间窗 | 牛熊转换止损盘第几天 | 无对象 | ❌ | **缺** |
| 量能微观 | 分时缩转放、中继量能 | `wave_breb_watch`（分钟）、`wolf_volume_turn_alert`（3 分钟） | ❌ 只推 QQ | **有数据没进** |
| 筹码成本 | 1200E 被套 −20% | 成交额/换手/公募拥挤快照 | 部分（confirm_context 拥挤过滤） | **缺成本分布** |
| 资金主体 | GJD 宽基、险资、两融 | `macro_state` | ✅ macro_context | **有** |
| 宏观 | 加息周期 | 宏观日历/开关 | ✅ 部分 | **半缺（无利率路径）** |
| 子链强弱 | 材料>设备、PCB 国产替代 | theme L2 只有 watch/trial 标签 | 部分（main_line_context） | **缺时序强弱** |
| 辨识度/龙头 | 龙头/龙2/二供、龙虎榜 | stock_pool、涨停、leader agent | 部分 | **半缺** |

## 四、结论：先补输入，再谈胆量

重写 agent 之前必须先做四件事（都是加字段，不是加勇气）：

1. **把 `b_rebound_daily` 注入 `node_fetch_context`**（一行 context），让“双脚/缩转放/数三/仓位建议”进入 agent 视野；
2. **修掉 3 处读已删文件的浪型读取**，统一走 `core.wave`（PG）；
3. **补 `chip_cost`**：区间换手加权成本 + 被套比例（用 `mkt_bars_daily` 可算，不猜）；
4. **补 `subchain_strength`**：半导体材料/设备/PCB/覆铜板 相对主线的超额与 breadth 时序。

验收用 11/04：这四项在输入包里齐不齐。**不齐就不动提示词和仓位预算**——否则“不保守”只会变成“没依据地下手”，比现在更糟。
