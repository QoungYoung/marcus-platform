# dead episode 分析 v2（扩展宇宙 + 复合 episode）

> 2026-10-22；PIT 宇宙扩到 18 主题，并修复 episode id 只按全局编号的问题。

## 扩展

- 新增主题：光伏、航空装备；
- 新增 segment：电力/公用 → 火电、水电；
- 储能：仍无直接 PIT concept；
- 2024：无 membership，不可扩。

## 修复

原统计：

```python
df.dead_episode.nunique()
```

只数了全局 episode id，导致 222 个 dead 段被报成 10。

修复为：

```python
df.groupby(["theme","segment_role","dead_episode"]).ngroups
```

结果：**222 个 dead 段**。

## dead episode 分析

```text
可计算 episode 168
confirmed_dead 156
rebound 12
```

| 指标 | 均值 | 最差 | 胜率 |
|---|---:|---:|---:|
| second | +0.14% | -30.97% | 48.7% |
| high_cut_low | **-0.66%** | -20.71% | 50.6% |
| second 尾部 | -2.19% | -33.73% | 35.9% |

## 解释

- 只取 **dead onset + T+1~T+3 反弹失败确认**；
- confirmed dead 后高切低均值 **-0.66%**，方向符合狼大；
- second 本身仍 +0.14%，说明 dead 不等于“所有二线都跌”；
- 尾部 second 平均 -2.19%，最差 -33.7%，风险在尾部；
- 12 个 rebound（3 日内收回 last_high）被排除后，HCL 仍负。

## 对比

全 dead 周行：

```text
dead second +0.30%
dead hcl    +0.07%
```

差别说明：

> **dead 不能按所有周平均；要看 onset + 反弹失败确认。**

## 结论

```text
dead 段数 222          ✅ 样本够了
confirmed dead HCL     ✅ -0.66%
dead second            ⚠️ 仍为正
尾部风险               ✅ 明显
```

动作层可以开始考虑：

```text
dead confirmed → 禁止高切低
dead confirmed → 不必然禁止所有二线
```

生产仍只读 advisory，不接 gate。
