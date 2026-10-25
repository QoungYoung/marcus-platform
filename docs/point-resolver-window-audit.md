# 窗口审计：我们到底给 agent 的是哪个时点？

> 结论先说：**之前没有统一的窗口字段。G/H 实际是“收盘后”，I/J 是“盘中快照但字段叫 close”。agent 没有被明确告知现在是否收盘。**

## 1. 各轮实际窗口

| 轮次 | case | 输入里的数据 | 实际窗口 | 问题 |
|---|---|---|---|---|
| A-F | 2025-06/11/12 | 日线 `close` = 当日最终收盘；`recent_bars` 含完整当日 bar | **收盘后** | 用“结构性破位”规则尚可；但 B/C 的 3×15min 语义没有盘中数据 |
| G/H | 2025-12-11 / 2026-01-14 | 同上，`close` = 最终收盘 | **收盘后** | 动作是盘中/3×15min，却用收盘后数据；agent 看到的是全天 low/high/close |
| I/J（第一次） | 2025-09-04 / 2025-08-01 | 同上，全天日线 | **收盘后** | 3732.84 是 14:35 才出现的低点，lookahead |
| I/J（PIT 重测） | 同上 | `decision_time`=14:30/13:15；`today_ohlc` 只到该时点；`close`=该时点价 | **盘中快照** | 字段仍叫 `close`，没有 `phase`；agent 可能当成收盘 |

## 2. agent 有没有“以为已经收盘”？

- **G/H**：它看到的是最终 `close` 和完整当日 `high/low`，所以它的推理实际上是在**收盘后**做的；它选 3936/4034 时，用的是全天已走完的数据。
- **I/J PIT**：它看到了 `decision_time` 和 partial `today_ohlc`，所以它部分知道是盘中；但字段名 `close`、没有 `phase`，语义不够硬。
- 不能怪 agent“以为收盘”；是我们的 schema 没有明确告诉它。

## 3. 正确的快照 schema

```json
{
  "asof": "2025-09-04",
  "phase": "intraday",          // intraday|close|post_close|replay
  "decision_time": "14:30",
  "session_open": true,
  "is_final": false,
  "last_price": 3735.36,        // 不再是 close
  "today_ohlc": {               // 只到 decision_time
    "open": 3807.76, "high": 3808.40, "low": 3733.34, "last": 3735.36
  },
  "bars_cutoff": "14:30",
  "data_warning": "今天未收盘；today_low/high 只到 14:30，不是全天最低/最高"
}
```

- `phase=intraday`：禁止用“收盘”语言，禁止假设 today_low 是全天最低；
- `phase=close`：才可用最终 close；
- `phase=post_close`：收盘后复盘，不能假装盘中；
- `phase=replay`：只用 as-of 及以前。

## 4. 必须重跑

- G/H 如果要测盘中动作，必须按 `phase=intraday` + 当时时点重跑；
- I/J 已有 PIT 数据，但要加 `phase`/`last_price` 字段重跑；
- 否则 A-F/G/H/I/J 的结果都混了窗口，不能横向比较。

一句话：**之前不是“没有窗口”，是窗口藏在字段里、没告诉 agent；G/H 实际是收盘后，I/J 是盘中但字段叫 close。**
