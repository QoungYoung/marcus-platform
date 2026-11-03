# 离场规则规格（狼大口径）

> 目的：把“聊天里说清楚”的离场逻辑，落成**可执行定义 + 数据契约 + 输出契约**。
> 原则：**原话 / 标定 / 代理** 三级标注；代理不得冒充原话。
> 本规格先覆盖：板上减半、压力位减半、加速结束、跌破支撑、清剩余。

## 0. 总则

1. 先减半，剩下吃溢价；加速结束才清剩余。
2. 卖点优先看位置/形态/量能/破线，不用固定百分比止损。
3. 指数级破位才是硬止损；个股级默认提示/减仓。
4. 所有规则必须可回放：同一份 `snapshot` 输入，输出必须确定（agent 判断另存）。

## 1. 语料依据

| 规则 | 狼大原话 | 日期 |
|---|---|---|
| 板上减半 | 「吃一口减一半 安全第一 渣男板块 这是没减之前的图 板上减了」 | 2026-09-01 |
| 压力位/上轨减半 | 「触及上方大级别压力位(如 BOLL 上轨)时，逢高卖出部分底仓、锁定利润」 | 2026-04-29 |
| 压力位先卖一半 | 「偏离太多 先卖一半 等回归 BOLL 轨内再接回」 | 2026-01-13 |
| 剩下吃溢价 | 「底部买 突破减半 剩下的吃溢价」 | 2025-12-24 |
| 加速结束清仓 | 「我已经减半了 等加速结束然后清仓 等一下周期」 | 2025-09-09 |
| 二次冲高失败 | 「第二次冲高半小时如果缩量不过前高 那就果断走」 | 2026-08-04 |
| 破黄线 | 「在这个半小时内有个绝对不能破的点 就是日均线那条黄线 一旦突发跌破直接走」 | 2026-08-04 |
| 多次冲高不过 | 「第一波接近高点多次突破不过去就已经说明问题了 一旦回落 反手就可以兑现」 | 2026-08-13 |
| 放量滞涨 | 「高位这些 PCB/CPO 都是放量滞涨 其实就是交换筹码」 | 2026-05-11 |
| 跌破支撑才卖 | 「可以不追高 但是卖点只有跌破支撑位」 | 2025-12-25 |

## 2. 数据契约

### 2.1 snapshot

```jsonc
{
  "symbol": "SH600299",
  "asof": "20261103",
  "decision_time": "11:06",
  "data_cutoff": "11:06",
  "bars_5m": [
    {"time":"202611030935","open":8.20,"high":8.51,"low":8.20,"close":8.35,"vol":237904}
  ],
  "quote": {
    "current":8.52,"open":8.20,"high":8.63,"low":8.12,
    "pre_close":8.09,"average":8.449,"vol":910643,"amount":76926
  },
  "position": {
    "original_volume":1000,"current_volume":500,"sellable":500,
    "avg_cost":8.10,"sold_today":0,"sold_since_entry":500,
    "remaining_cycle_budget":500,"board_half_done":false
  },
  "levels": [
    {"id":"prior_high_8.51","price":8.51,"type":"prior_high"},
    {"id":"vwap_8.449","price":8.449,"type":"vwap"}
  ],
  "warnings": ["bar_5m_last_incomplete"]
}
```

- `bars_5m` 只允许 **已收盘** 桶；末桶未完成必须剔除或标 warning。
- 时间统一 `YYYYMMDDHHMM`；降级数据源格式不统一时先归一。
- `quote.average` 是腾讯黄线；缺失时用 `amount*100/vol` 估算并标 warning。

### 2.2 输出

```jsonc
{
  "decision":"hold|reduce_half|exit_all|exit_on_break",
  "signal":"board_half|pressure_half|accel_end|support_break|none",
  "features": {...},
  "reason":"...",
  "confidence":0.0,
  "needs_agent":false,
  "warnings":[]
}
```

## 3. 规则定义

### R1 板上减半

- 条件：`day_gain_pct >= board_threshold`（10cm 9.5%，20cm 19.5%）且 `pnl_pct >= 3%`。
- 量：当前持仓的 50%，且不超过 `remaining_cycle_budget`。
- 约束：一个持仓周期只做一次；压力位减半计入同一周期预算。
- 状态：`decision=reduce_half`, `signal=board_half`。
- 依据：原话；阈值 9.5/19.5/3% 为仓库既有标定。

### R2 压力位减半

- 条件：价格触及/上穿给定压力位，且浮盈>0。
- 量：50%，与 R1 共用周期预算。
- 若无法确认压力位：`needs_agent=true`，不自动卖。
- 依据：原话“逢高卖出部分/先卖一半”。

### R3 加速结束

#### 3.1 结构识别（确定性）

- 使用已收盘 5m 桶。
- **push1**：09:30–10:30 内第一个局部高点；局部高点定义 `high[i] >= high[i-2..i+2]`。
- **pullback**：push1 之后、push2 之前的最低点；回撤幅度 `(push1-pullback)/push1`。
- **push2**：pullback 之后的下一个局部高点。
- `push2_vs_push1_pct = (push2/push1-1)*100`。
- 若找不到 push1/pullback/push2：`accel_end=false`。

#### 3.2 失败条件

满足以下 **A + B + C**：

A. 二选一：
   - `push2 <= push1 * 1.001`（没有有效突破前高）→ pattern=`accel_end_2nd_push_failed`；
   - `push2 > push1 * 1.001`，但 push2 后同样满足 B+C（突破后失败）→ pattern=`accel_end_after_break`。

B. push2 后至少 **6 根已收盘 5m**（30 分钟），且：
   - 后续最高 `<= push2 * 1.001`；
   - 后续 6 根均量 `< push2 前 6 根均量 * 0.8`（0.8 为代理）。

C. 价格确认，满足任一：
   - `current < vwap * (1 - 0.005)`（黄线破位，0.5% 为代理）；
   - 或连续 2 轮 5m 收盘在黄线下方；
   - 或 `current < push2 * 0.99`（1% 回撤为代理）。

#### 3.3 特例

- **安迪苏 11/3**：push1=8.51，pullback=8.28，push2=8.63 **> push1**，所以不是“不过前高”。  
  它属于 **push2 后失败**：push2 后 30 分钟无新高 + 量缩 + 跌破 VWAP/回撤。  
  → 规则应输出 `accel_end`，但 reason 必须写“push2 后失败”，不能写“不过前高”。
- **东方海洋 11/3**：push1=3.42，pullback=3.35（10:05 5m 低点），push2=3.42；封板状态优先，`accel_end=false`。
- **封板/炸板**：`is_sealed=true` 时禁止 R3；炸板未回封时 R3 至少需要黄线确认，否则 `needs_agent=true`。

### R4 跌破支撑

- 条件：收盘/确认跌破给定支撑，且非盘中假破。
- 量：清剩余（指数级破位才清全部）。
- 依据：原话“卖点只有跌破支撑位”。

### R5 清剩余

- `exit_all` 必须卖 **sellable 全部**，不得被 `WOLF_V3_MAX_SYMBOL_PCT=0.6` 截断。
- 仍走网关：`volume <= sellable`；底仓 floor 由 `position_sizing` 决定。
- ≤150 股必须整笔可卖；否则返回明确 `blocked_reason`，不得静默失败。

## 4. Agent 接口

### 4.1 何时调用

只在确定性层已算出候选（`accel_end_candidate` 或 `pressure_unknown`）时调用；不每 30 秒全仓调用。

### 4.2 输入

在 snapshot 基础上增加：

```jsonc
{
  "features": {
    "push1": {...}, "pullback": {...}, "push2": {...},
    "push2_vs_push1_pct": 1.41,
    "minutes_since_push2": 95,
    "vol_ratio_after": 0.63,
    "vwap": 8.449, "vwap_state": "above",
    "board": {"limit_price":8.90,"is_sealed":false,"open_count":0,"refilled":false}
  }
}
```

### 4.3 输出

```jsonc
{
  "pattern":"accel_end_2nd_push_failed|accel_end_after_break|vwap_break|board_open_no_refill|none",
  "exit_intent":"hold|exit_all|exit_on_break",
  "evidence":["只能引用 features 中字段"],
  "confidence":0.0,
  "invalidate_if":["..."]
}
```

### 4.4 校验器

- `pattern` 必须在白名单（含 `accel_end_after_break`）；
- `evidence` 字段必须存在于 features；
- 价格/量/时长由确定性层重算，agent 只引用；
- `exit_intent=exit_all` 必须经过 R5；
- 超时/坏 JSON/schema 不符 → hold + alert；
- 幂等键 `(date,symbol,pattern,anchor_id)` 写 PG。

## 5. 明确标注的代理

| 代理 | 值 | 来源 |
|---|---|---|
| 板上阈值 | 9.5% / 19.5% | 仓库既有标定 |
| 浮盈阈值 | 3% | 狼大“浮盈≥3%” |
| push2 容差 | 0.1% | 代理 |
| 30 分钟 | 6 根 5m | 原话“半小时”，实现代理 |
| 量缩 | 0.8 | 代理 |
| 黄线破位 | 0.5% / 2 轮 | 代理 |
| 回撤 | 1% | 代理 |
| 横盘/平台 | 未定义 | 待标定 |

## 6. 验收

1. 四类合成用例：双头失败、开盘冲高回落、炸板未回封、一字板。
2. 真实回放：安迪苏 11/3、东方海洋 11/3 + 至少 20 个历史加速票。
3. 影子 2 周：记录规则/ agent / 人工三者差异。
4. 通过后再开 `WOLF_ACCEL_END_LIVE=1`。
