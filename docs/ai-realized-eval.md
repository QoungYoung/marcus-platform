# AI 状态 vs 真实 5 日结果

> 2026-10-22；不看与机械一致率，直接看未来 5 日。

## 口径

- 36 case 中可计算 30 个；
- 每个 case 取 leader 和二线，算 5 日：
  - second = 二线等权 5 日
  - hcl = second - leader
- 按 AI 状态 / 机械状态分组。

## AI 分组

| AI 状态 | n | second 5日 | hcl 5日 |
|---|---:|---:|---:|
| alive | 8 | **+3.52%** | -0.10% |
| rotated | 5 | +1.89% | -0.32% |
| warning | 17 | **+1.04%** | -0.51% |

AI alive 组明显最好，warning 组最低。

## 机械分组

| 机械状态 | n | second 5日 | hcl 5日 |
|---|---:|---:|---:|
| alive | 8 | **+3.52%** | -0.10% |
| rotated | 6 | +2.53% | -0.89% |
| warning | 9 | +0.93% | -0.67% |
| dead | 7 | **+0.51%** | +0.16% |

机械 dead 组不是最差，反而是正收益。

## 关键结论

1. **AI alive 组真实 5 日最好**：+3.52%，支持 AI 判 alive；
2. **AI warning 组 +1.04%**，明显低于 alive；
3. **AI 没有 dead 组**，所以 dead 仍无法验证；
4. **机械 dead 组 +0.51%，不是负的**，机械 dead 很可能误标；
5. 所以“AI 和机械不一致”不是问题，机械 dead 本身可能就不真实。

## 能得出什么

```text
AI alive            ✅ 真实结果支持
AI warning          ⚠️ 低于 alive，但不能证明 dead
AI dead             ❌ 无样本
机械 dead           ❌ 未来 5 日不差，疑似误标
```

## 不能得出什么

- 不能说 AI 全面比机械准；
- 不能说 AI warning 等于 dead；
- 30 个 case、5 日、重叠，样本不足；
- 没有真实人工标签，5 日只是可观察代理。

## 决定

```text
生产                  advisory
AI                    只 shadow
AI alive              可以继续观察
AI dead               仍不可用
机械 dead             需要重新定义
```
