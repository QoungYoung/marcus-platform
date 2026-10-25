# 触发线 vs 支撑线：已破位就明确说“已破位”

> 用户口径：**我们不是只找支撑；如果已经破位，就提示已破位。**
> 这条用来修 B/C/I 的问题：agent 把“上方已破的触发线”换成了“下方最近还没破的支撑”。

## 1. 两个字段，不能混

| 字段 | 含义 | 可以在现价上方？ | 可以已破位？ |
|---|---|---|---|
| `break_trigger` | **跌破/站不回它，就触发减仓/防守的线** | 可以 | 可以（常常已破） |
| `next_support` | 下方下一道还没破的支撑 | 不可以（必须在下方） | 不可以 |

`risk_trigger` 必须取 `break_trigger`，不能取 `next_support`。

## 2. 状态文案

| state | status_text | 含义 |
|---|---|---|
| `broken` | **已跌破 X（现价 Y 在其下方）** | 线在上方，价格已经收在下面 |
| `holding` | 下方支撑 X | 线在下方，还没破 |
| `resistance` | 上方压力 X | 上方未收复，但不是“已破的支撑” |
| `reclaimed` | 已站回 X | 重新收回，失效条件解除 |

## 3. 判例

### B/C：3922 是 break_trigger，3835 是 next_support

- 14:55 现价 3834.97；
- 3922 在现价上方 2.3%，state=`broken`；
- 3835.36 是下方 0.01% 的 10/17 swing low，state=`holding`。

正确输出：

```json
{
  "break_trigger": {"price": 3922.58, "state": "broken", "status_text": "已跌破 3922.58（现价 3834.97 在其下方）"},
  "next_support": {"price": 3835.36, "state": "holding"},
  "risk_trigger": "break_trigger"
}
```

错误输出：把 3835.36 当 risk_trigger（“最近支撑”）。

### I：3761 是 break_trigger，3733 是 next_support

- 14:30 现价 3735.36；
- 3761.42 在现价上方 0.7%，state=`broken`；
- 3733.34 是下方 0.05% 的今日低点，state=`holding`。

正确输出：`break_trigger=3761.42（已跌破）`，`next_support=3733.34`。

### J：3560 是 break_trigger，3550 是 next_support

- 13:15 现价 3559.25；
- 3560 整数在现价上方 0.02%，state=`broken`（刚跌破/正在测试）；
- 3550 是下方下一道整数/低点。

正确输出：`break_trigger=3560`，`next_support=3550`。

## 4. 代码

`wolf_stop_layer.StopDecision` 已加：

- `level_state`：holding/broken/resistance；
- `status_text`：已跌破/下方支撑/上方压力；
- `format_level_status(level, close)`：统一生成文案。

`t_monitor` 的 v3 事件日志已带 `level_state` / `status_text`。

## 5. 一句话

**支撑线可以找，但不能替代触发线；触发线在上方且已破，就明确写“已跌破”，不能换成下方最近支撑。**
