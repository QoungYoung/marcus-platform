# AI 风向标提示词 v4

> 2026-10-22；36 case；补 cycle 级 `event_lower_high_count` / `event_volume_distribution`。

## 提示词改动

```text
不再只看最近 20 日 lower highs
改用 cycle 级 event_lower_high_count
dead 必须 event_volume_distribution=true
rotated 必须 prev_state=dead + leader_changed + 新 impulse
alive 必须 close_vs_last_high >= -0.02 且非放量
```

## 结果

```text
case 36
有效 36
一致 25
一致率 69.4%
```

| 机械 \ AI | alive | warning | rotated |
|---|---:|---:|---:|
| alive | **10** | 0 | 0 |
| warning | 0 | **10** | 0 |
| rotated | 0 | 1 | **5** |
| dead | 0 | **10** | 0 |

## 关键问题

```text
AI dead recall = 0
```

机械 dead 的 10 个 case，AI 全判 warning。  
原因是：

```text
event_volume_distribution 多数为 false
```

也就是说，机械 dead 里很多是“跌破 cycle 线”，不是“放量出货”。

## 这说明什么

1. v4 在 alive/warning/rotated 上很整齐；
2. 但 AI 把 dead 全部否掉；
3. 不是提示词没写好，是**机械 dead 与 AI 的 dead 定义不一致**；
4. 谁对仍无法判定：
   - 如果狼大要求放量，机械 dead 是假阳性；
   - 如果跌破 cycle 线也算 dead，AI 漏判。

## 对比版本

| 版本 | 一致率 | dead recall |
|---|---:|---:|
| v1 | 38% | 1/10 |
| v2 | 64% | 4/10 |
| v3 | 58% | 0/10 |
| v4 | **69%** | **0/10** |

一致率提高，但 dead 召回没了。

## 结论

```text
提示词优化有效（69%）
但不能接受 dead recall=0
AI 不能直接接管 dead
```

## 决定

- 生产继续 advisory；
- AI 只做 shadow；
- 不再用同一批 36 case 调 prompt，避免过拟合；
- 下一步如果继续，只能先统一 dead 定义，再重测。
