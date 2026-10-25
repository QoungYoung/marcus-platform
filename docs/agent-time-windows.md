# 给 agent 的时间窗口协议

> 目的：先告诉 agent“现在是哪个窗口、这个窗口能不能成交”，再让它选点/判断。
> 不能再出现“14:50 实时价当收盘”“15:00 确认却假设当天卖出”这种混窗。

## 1. 窗口枚举

| phase | 时间 | 数据 | 能不能当天成交 | 用途 |
|---|---|---|---|---|
| `intraday` | 09:30-14:30 | 截至 decision_time 的 partial OHLC | 可以，但受 13:00-14:30 执行禁区限制 | 盘中确认/3×15min/放量 |
| `tail_preview` | 14:45-14:55 | 截至 14:55 的 partial OHLC | **不成交，只预警** | 准备、不写“已破位” |
| `tail_confirm` | 14:55-15:00 | 14:55 的 last_price / 尾盘价 | **可以，14:55-15:00 执行** | 收盘代理确认（非官方收盘） |
| `close` | 15:00 | 官方收盘价/最终日线 | **不可以** | 官方确认；动作放次日 |
| `next_day` | 次日 09:30-10:00 | 次日开盘/早盘 | 可以 | 官方收盘确认后的执行 |
| `post_close` | 15:00 后 | 最终日线 | 不可以 | 复盘、记录，不假装盘中 |

## 2. 动作模式 → 窗口

| 动作模式 | 决策窗口 | 执行窗口 | 说明 |
|---|---|---|---|
| `close_break`（收盘破位） | 14:55 `tail_confirm` 或 15:00 `close` | 14:55-15:00，或次日 | 二选一，不能混 |
| `reclaim_failure`（站不回） | 14:55 尾盘 / 15:00 收盘 | 尾盘或次日 | 同上 |
| `close_threshold`（明确收盘位） | 14:55 尾盘 / 15:00 收盘 | 尾盘或次日 | 同上 |
| `tail_confirm`（尾盘确认） | 14:45-15:00 | 14:45-15:00 | 当日 |
| `intraday_3x15`（3×15min） | 跌破时点 + 45min | 确认后；G 等反抽，H 确认后当日 | 当日，G 可延到反抽 |
| `time_specific`（定点，如 2:30） | 14:30 | 14:30 | 当日 |
| `volume_fast`（快速放量） | 放量跌破瞬间 | 即时 | 当日 |
| `rebound_exit`（反抽卖出） | 盘中反抽 | 反抽到带内 | 当日 |

## 3. 硬规则

1. **14:50 只能 `tail_preview`**：只预警，不写 `break_actions`，不卖。
2. **14:55 是 `tail_confirm`**：可以执行，但必须标“非官方收盘”，回测也只能用 14:55 数据。
3. **15:00 是 `close`**：官方确认，但**当天不能成交**；动作只能 `next_day`。
4. **13:00-14:30 是执行禁区**：条件成立也只预警，等早盘或尾盘。
5. **`intraday` 的 `today_high/low` 只到 cutoff**：不是全天最高/最低。
6. **`close` 才可以用最终 `close`**；其他 phase 一律用 `last_price`。
7. **agent 必须输出 `window_assumption`**：它以为现在是哪个窗口、能不能当天成交；和输入不一致就报错，不硬判。

## 4. 给 agent 的 snapshot schema

```json
{
  "phase": "tail_confirm",
  "decision_time": "14:55",
  "data_cutoff": "14:55",
  "is_final": false,
  "last_price": 3735.36,
  "today_ohlc": {
    "open": 3807.76,
    "high": 3808.40,
    "low": 3733.34,
    "last": 3735.36
  },
  "execution_window": ["14:55", "15:00"],
  "same_day_executable": true,
  "confirmation": "close_proxy",
  "data_warning": "14:55 不是官方收盘；today_low/high 只到 14:55"
}
```

官方收盘：

```json
{
  "phase": "close",
  "decision_time": "15:00",
  "is_final": true,
  "close": 3765.88,
  "execution_window": ["next_open", "next_early"],
  "same_day_executable": false,
  "confirmation": "official_close",
  "data_warning": "已收盘；当天无法成交，动作只能次日"
}
```

盘中：

```json
{
  "phase": "intraday",
  "decision_time": "10:35",
  "data_cutoff": "10:35",
  "is_final": false,
  "last_price": 3751.29,
  "today_ohlc": {"open": 3807.76, "high": 3808.40, "low": 3750.64, "last": 3751.29},
  "execution_window": ["after_3x15", "on_rebound"],
  "same_day_executable": true,
  "confirmation": "3x15",
  "data_warning": "盘中；today_low/high 只到 10:35"
}
```

## 5. 判例对应

| case | 模式 | 决策 | 执行 |
|---|---|---|---|
| A | rebound_exit | 盘中反抽 | 反抽到 3370/3375 |
| B/C/D/E | close_break / close_threshold | 14:55 尾盘 或 15:00 收盘 | 尾盘 或 次日 |
| F | tail_confirm | 14:45-15:00 | 尾盘 |
| G | intraday_3x15 | 跌破+45min | 等反抽 |
| H | intraday_3x15 + volume | 跌破+45min | 确认后当日 |
| I | time_specific | 14:30 | 14:30 |
| J | volume_fast | 放量瞬间 | 即时 |

## 6. 一句话

**先报窗口，再选点：14:50 只预览，14:55 尾盘代理可当天走，15:00 官方收盘只能次日走；盘中 3×15/定点/放量按各自窗口。**
