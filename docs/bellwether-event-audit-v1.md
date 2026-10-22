# 风向标事件审计 v1

> 2026-10-22；修复 dead 重复计数、impulse 周期、换人重置后的首版事件回测。

## 修复

1. `cycle_start` + `last_high` 双锚点；
2. leader 换人时重置 cycle；
3. 老 leader dead + 新 leader 接上 → rotated；
4. dead 按 episode 计，不按每周行数；
5. low 缺失用 10% 失效线兜底。

## 事件计数

```text
rows 3895 / 93 dates / 15 themes
alive 261
warning 925
dead 2525（episodes 12）
rotated 70
unknown 114

second_catchup 183
high_cut_low 145
```

> dead 行数仍高，是状态持续；独立 dead episode 为 12。

## 动作级 5 日结果

| state | second | high_cut_low |
|---|---:|---:|
| alive | **+1.47%（t3.03, n230）** | **+1.21%（t2.40）** |
| warning | -0.15%（n761） | +0.20%（n761） |
| dead | +0.34%（n2011） | +0.17%（n2011） |
| rotated | +0.40%（n57） | **-0.99%（n57）** |

事件子集：

```text
补涨事件 +1.45%（t2.46, n183）
高切低事件 +1.52%（t2.03, n145）
```

## 结论

```text
alive 补涨/高切低     ✅ 有初步支持
rotated 高切低        ✅ 支持禁止
warning 补涨          ⚠️ 无超额
dead 补涨/高切低      ❌ 仍为正，不能证明“全停”
```

关键：**dead 结果与狼大“死了就停”不一致，说明 dead 定义或动作条件仍不对，不能直接上 gate。**

## 限制

- dead episodes 只有 12，且 dead 动作反事实为正；
- 事件阈值（8%/5pp/12%）是首版，未做敏感性；
- 5 日重叠；
- PIT low 有近似；
- rotated 样本 57。

## 决定

生产继续只读 advisory；动作层不接入。  
下一步应单独校准 dead，而不是继续优化 alive。
