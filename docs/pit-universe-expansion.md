# PIT 宇宙扩展 v1（2025-2026）

> 2026-10-22；2024 无 membership，改扩 2025-2026 主题。

## 2024

```text
20240105 membership MISSING
labels = 0
```

提供方没有 2024 `dc_member` 快照，不能做 PIT。

## 2025-2026 扩展

在 `jobs/build_pit_chain_map.py` 加 **PIT-only** `EXTRA_SEED`，不改生产 `chain_map.SEED`：

```text
光伏
储能
火电
水电
航空装备
```

离线重建：

```text
91 个日期
18 个主题
```

新增覆盖：

| 主题 | 行数 | 状况 |
|---|---:|---|
| 光伏 | 546 | ✅ |
| 航空装备 | 182 | ✅ |
| 储能 | 2 | ⚠️ concept 覆盖不足 |
| 火电 | 0 | ❌ 无 concept |
| 水电 | 0 | ❌ 无 concept |

## dead/动作

```text
dead 行数 2968
dead episodes 10
second_catchup 181
high_cut_low 149
```

动作回测（扩展后）：

| state | second | hcl |
|---|---:|---:|
| alive | +1.16% | +0.99% |
| warning | -0.44% | +0.33% |
| dead | +0.29% | +0.06% |
| rotated | +0.10% | -0.62% |

## 结论

```text
2024 扩不了           ❌ 数据不存在
主题扩到 18           ✅
dead episodes 10      ❌ 仍不够
```

新增主题没有显著增加 dead episode，原因：

1. 火电/水电 concept 缺失；
2. 储能只有 2 行；
3. 新主题只有一个 segment，事件密度低；
4. dead 是稀有事件，增加 3 个主题不够。

## 下一步

不是继续加主题，而是：

- 给已有主题增加 **segment**；
- 补火电/水电/储能的 concept 映射；
- 或接受 dead 只能定性，不做统计验收。
