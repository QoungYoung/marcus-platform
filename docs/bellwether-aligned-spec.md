# 风向标对齐规格 v1

> 2026-10-22  
> 目标：把风向标从“主题状态因子”改成“龙头生命周期 + 动作条件”。  
> 本规格只定义事件和动作，不定义选股排序/仓位乘数。

## 0. 一句话

**先有龙头 impulse，才有风向标状态；状态只决定“补涨/高切低/持有”能不能做。**

---

## 1. 语料依据

| 语料 | 约束 |
|---|---|
| 「龙头风向标死了就不能做了……千万不要想高切低，麻溜的跑」 | dead 时：禁止补涨、禁止高切低、降暴露 |
| 「风向标没有死，自己的票没涨也拿着等补涨」 | alive 时：允许持有弱势二线、等补涨 |
| 「先从风向标看起，再看核心有没有反方向」 | 顺序：先看龙头，再看核心 |
| 「被摁跌停或更换了，换得快、接得上，只影响短期」 | 换人 ≠ 主题死；rotated 要单独处理 |
| 「风向标是拿来看的，不是拿来做的」 | 不买风向标本身，不做选股因子 |

---

## 2. 基本对象

### 2.1 龙头 leader

- 来源：PIT `labels_weekly` 中 `rank=1`；
- 同一主题可有多个 segment 的 rank1，按 segment 分别保留；
- 不把 `bucket=second` 直接当“补涨”，second 只是候选池。

### 2.2 Impulse

Impulse 是状态机的锚点，不是每周固定重算。

定义（任一 segment）：

```text
leader 当日 high > 前一次 impulse high
且 leader 当日成交额 >= 近 20 日均额 * 1.2
且 leader 当日相对主题收益 > 0
```

输出：

```text
impulse_high
impulse_date
```

后续状态都相对 `impulse_high` 判断。

### 2.3 二线滞后

二线不是所有 `bucket=second`，必须满足：

```text
leader 自 impulse 以来涨幅 - second 同期涨幅 >= lag_min
```

建议 `lag_min = 5pp`，作为参数显式记录。

---

## 3. 状态机

### alive

满足：

```text
有 impulse
且 close >= impulse_high * 0.995
且 未出现放量出货
且 无更低高点
```

动作权限：

```text
✅ 新做补涨
✅ 高切低（仅当 leader extended）
✅ 持有弱势二线
```

### warning

满足：

```text
有 impulse
且（close < impulse_high * 0.995 或 出现更低高点）
且 未跌破 impulse 低点
且 未出现放量出货
```

动作权限：

```text
❌ 新做补涨
❌ 高切低
✅ 持有已有
```

### dead

满足任一：

```text
close < impulse_low
或 高点递减 + 放量出货
或 龙头跌停/接近跌停
```

动作权限：

```text
❌ 新做补涨
❌ 高切低
❌ 新买该主题
✅ 降暴露/离场
```

### rotated

满足：

```text
老 leader dead
且 同一主题出现新 leader（rank1 更换）
且 新 leader 有独立 impulse
```

动作权限：

```text
按新周期重新判断
不得把 rotated 直接当 dead
也不得在未确认新 impulse 前做补涨
```

### unknown

```text
数据不足 / 无 impulse / 无 leader
```

动作权限：

```text
❌ 补涨
❌ 高切低
✅ 仅观察
```

---

## 4. 动作事件

### A. 补涨 second_catchup

触发：

```text
state == alive
leader 自 impulse 涨幅 >= 8%
second 滞后 >= 5pp
second 当日未涨停/未放巨量
```

动作：

```text
买入 second，持有 5 个交易日
```

退出：

```text
5 日到期
或 state 转为 warning/dead
或 second 跌破入场日低点
```

### B. 高切低 high_cut_low

触发：

```text
state == alive
leader extended：
  leader 5日涨幅 >= 12%
  或 leader 距 MA20 >= 15%
second 滞后 >= 5pp
```

动作：

```text
卖出/减 leader，买入 second
```

禁止：

```text
warning / dead / unknown 时不得触发
rotated 未确认新 impulse 前不得触发
```

### C. 持有弱势二线 hold_weak_second

```text
已持有 second
state == alive → 持有
state == warning → 减半/不再加
state == dead → 离场
```

---

## 5. 与旧实现的差异

| 旧实现 | 新规格 |
|---|---|
| 每个周度日期都算状态 | 只能在 impulse 后算 |
| 所有 second 都算补涨候选 | 必须龙头已涨 + 二线滞后 |
| second - leader 就是高切低 | 必须 leader extended + alive |
| warning/stale 自创并统一挡 | warning 只禁新做，不禁持有 |
| 状态挂到所有 picks | 状态只挂在动作事件上 |
| 用 5 日收益证明状态 | 用动作事件结果 + 合规证明 |

---

## 6. PIT 映射

| 规格对象 | PIT 数据 |
|---|---|
| leader | `labels_weekly.rank=1` |
| second | `labels_weekly.bucket=second` |
| 主题成员 | `theme_members/observations.csv`，缺失时 labels |
| 价格/量 | `bars_api.parquet` / `bars_combined.parquet` |
| 状态 | 由本规格事件机重算，不用现成 state 文件 |
| 换人 | 相邻 PIT 日期 rank1 变化 |

已知限制：

- PIT labels 只含 top ranks，不是全成员；
- `observations` 2025-08 前不完整；
- 部分日期 high 用 close 补；
- 5 日收益重叠；
- 无盘中数据，跌停/放量只能用日线近似。

---

## 7. 回测口径

### 主指标

```text
alive 补涨事件 5日收益
alive 高切低事件 5日收益
warning/dead 动作违规数
误杀率：被禁止但后来上涨的比例
```

### 验收

```text
n >= 30
alive 动作 5日 > warning/dead
方向在两个独立窗口一致
dead 时补涨/高切低事件 = 0
```

### 不验收

```text
blocked/allowed 全 picks 收益
状态作为选股因子
position_multiplier
```

---

## 8. 实施顺序

1. 写事件提取器：`jobs/build_bellwether_events.py`；
2. 输出事件表：`data/core_pit/bellwether_events.csv`；
3. 写动作回测：`jobs/backtest_bellwether_events.py`；
4. 先跑合规，再跑 5 日；
5. 通过后才考虑动作层接入；
6. 生产 `select_core` 保持完全不看风向标。
