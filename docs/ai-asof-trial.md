# AI 风向标 as-of 试跑

> 2026-10-22；4 个典型 case；只喂截至当日事实，不喂机械结论。

## 约束

- 输入只含 as-of 价格/量/高点/MA20/二线，不含未来；
- prompt 不含 `mechanical_state`；
- 每次新建 session，避免历史污染；
- AI 只输出 state/evidence/action_permissions，不做收益预测。

## 结果

| 主题 | 日期 | 机械 | AI | 置信度 |
|---|---|---|---:|---:|
| 光伏 | 20260918 | warning | warning | 0.78 |
| 传媒/游戏 | 20260828 | **dead** | **warning** | 0.72 |
| 电力/公用 | 20260918 | warning | warning | 0.60 |
| AI/算力/科技 | 20260618 | **dead** | **alive** | 0.84 |

## 关键分歧

### 传媒/游戏 20260828

机械 dead。  
AI 认为：

```text
高点连续下移成立
但下跌段缩量，没有放量出货
→ dead 不确认，降 warning
```

这正是我们之前 dead 机械条件缺的“放量确认”。

### AI/算力/科技 20260618

机械 dead。  
AI 认为：

```text
寒武纪当日 +14.2%，创 20 日新高，成交额 1.86 倍
→ impulse，不是 dead
```

机械状态用了旧高点链，漏掉了突破日。

## 结论

```text
AI 能抓到机械 dead 的假阳性     ✅ 2/4
AI 与机械一致                  ✅ 2/4
AI 不是收益预测                 ✅
```

但：

- 只有 4 个 case；
- 没有人工 ground truth；
- 可能过度依赖 prompt 措辞；
- 不能证明 AI 状态更准。

## 当前决定

```text
AI 只做裁判/shadow
不参与选股
不接 gate
生产继续 advisory
```

## 下一步

- 扩到 20-30 个 case；
- 人工标注“狼大会怎么判”；
- 比较机械/AI/人工三方；
- 只在这之后决定是否用 AI 替换机械 dead。
