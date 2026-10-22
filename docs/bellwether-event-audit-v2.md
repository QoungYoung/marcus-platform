# 风向标事件审计 v2（对齐 dead 定义）

> 2026-10-22；dead 改为“连续 lower highs + 放量”，删除 10% 自创失效线；dead 持续到新 impulse/新龙头。

## 修复

```text
lower_high_count >= 2
且 放量
→ dead

删除 10% 兜底线
low 缺失不硬判 dead
dead 持续，直到：
  新 impulse
  或 leader 更换
```

## 事件

```text
rows 3895
alive 255
warning 594
dead 2870（episodes 10）
rotated 62
unknown 114

second_catchup 178
high_cut_low 143
```

## 5 日动作结果

| state | second | high_cut_low |
|---|---:|---:|
| alive | **+1.32%（t2.62, n225）** | **+0.97%（t1.82）** |
| warning | **-0.36%（n502）** | +0.48% |
| dead | **+0.35%（t2.73, n2282）** | +0.12% |
| rotated | +0.24%（n50） | **-0.62%（n50）** |

事件子集：

```text
补涨 +1.32%（t2.10, n176）
高切低 +1.30%（t1.62, n141）
```

## 结论

```text
alive 补涨            ✅ 支持
warning 补涨          ✅ 负，支持禁止
rotated 高切低        ✅ 方向支持，t 不足
dead 补涨/高切低      ❌ 仍为正，不支持“全停”
```

dead 仍是核心问题：

- episodes 只有 10；
- dead 状态下二线 5 日 +0.35%，不是负；
- dead 的“停做”没有数据支持。

## 限制

- dead 状态持续，行数仍重复；
- 连续 lower high 的识别依赖 PIT 周度采样；
- 5 日重叠；
- low 缺失；
- rotated n=50。

## 决定

```text
生产 select_core：继续 advisory
动作层：不接入
下一步：只校准 dead，不碰 alive/warning
```
