# AI 风向标验证 v2：补 prior state / leader_changed / impulse

> 2026-10-22；36 个 as-of case；不是人工 ground truth。

## 改动

给 AI 补：

- `previous_state`
- `prev_leader`
- `leader_changed`
- `cycle_start / last_high`
- `leader_ret_since_cycle`
- `second_lag`
- `extended`

不再喂当前机械状态。

## 结果

```text
case          36
有效          36
与机械一致     23
一致率        63.9%
```

混淆矩阵：

| 机械 \ AI | alive | warning | dead | rotated |
|---|---:|---:|---:|---:|
| alive | 5 | 3 | 0 | 2 |
| warning | 0 | 10 | 0 | 0 |
| dead | 0 | 6 | 4 | 0 |
| rotated | 0 | 1 | 1 | 4 |

v1 一致率 38.2% → v2 63.9%。

## AI 仍然偏 warning 的原因

1. prompt 规则：dead 必须有放量；没有放量 → 不能 dead；
2. alive 必须有 impulse/新高；没有 → 不能 alive；
3. 两者都不满足 → 默认 warning；
4. 二线/换人信息仍不完整。

## AI 的理由是否正确

### 机械 dead → AI warning（6 个）

这些 case 的 `vol_ratio5_20` 多数 < 1.0：

```text
缩量下跌
→ 没有放量出货
→ dead 不确认
```

理由符合规则，**AI 很可能对，机械 dead 是假阳性**。

### 机械 alive → AI warning（3 个）

```text
close < high20
lower_high_days 高
```

AI 认为没有新高，不能 alive。  
机械 alive 可能基于旧 impulse，**AI 不一定错**。

### 机械 alive → AI rotated（2 个）

AI 看到 `leader_changed=true` 就判 rotated。  
但规则要求：

```text
previous_state == dead
+ 新 leader 有独立 impulse
```

这两个 case 的 prev_state 是 alive，**AI 过度 rotated**。

### 机械 dead → AI alive（0 个）

v2 没有 dead→alive，改善了。

## 加 guard 后

后处理：

```text
ai rotated 但 prev_state != dead → 降为 warning
ai alive 但 close < high20*0.98 → 降为 warning
```

结果：

| 机械 \ AI | alive | warning | dead | rotated |
|---|---:|---:|---:|---:|
| alive | 5 | 5 | 0 | 0 |
| warning | 0 | 10 | 0 | 0 |
| dead | 0 | 6 | 4 | 0 |
| rotated | 0 | 1 | 1 | 4 |

一致率仍 23/36 = 63.9%，但错误类型更可控：

```text
AI dead 精度 4/4
AI rotated 4/6
AI warning 10/10
AI alive 5/10
```

## 谁对

不能一刀切，但可以分层：

| 场景 | 更可能对 |
|---|---|
| 机械 dead + 缩量 | **AI**（机械假阳性） |
| 机械 alive + 无新高 | **AI**（机械 alive 可能 stale） |
| 机械 rotated | **机械**（AI 仍需 guard） |
| AI alive | **不一定**，precision 低 |

## 结论

```text
AI 接管全部状态      ❌
AI 替代机械          ❌
AI 复核机械 dead     ✅ 有初步支持
AI 用在 alive        ❌ precision 不够
AI 用在 rotated      ⚠️ 需 guard
```

## 决定

- 生产继续 advisory；
- AI 只做 shadow；
- 只考虑“机械 dead 复核”，不做 alive/rotated 替换；
- 不接 gate。
