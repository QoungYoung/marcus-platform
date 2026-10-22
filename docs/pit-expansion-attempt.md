# PIT 扩展尝试：2024 与主题/segment 宇宙

> 2026-10-22

## 1. 2024 历史

尝试：

```text
python jobs/build_pit_chain_map.py --dates 20240105 --out-dir /tmp/pit2024
```

结果：

```text
membership MISSING (skip)
labels_weekly.csv = 0 rows
```

原因：

- 提供方没有 2024 的 `dc_member` 周度快照；
- builder 缓存最早从 `20250103` 开始；
- 没有 membership 就不能做 PIT，不能拿当前成员回填 2024。

结论：

```text
2024 PIT 不可用
```

不能用当前成员表冒充 2024，否则又是前视。

## 2. 当前 PIT 宇宙

```text
93 个周度日期
15 个主题
42 个 theme-segment
```

系统子主题有 49 个，但 PIT labels 不是子主题级，而是 theme-segment 级。

## 3. 扩大主题/segment

要增加主题，需要：

1. 在 `apps/main_line/chain_map.py` 的 `SEED` 增加主题/segment；
2. 给 segment 配 concept/行业成员；
3. 重建 PIT labels；
4. 重跑 dead episode。

不能只改 `subthemes.py`，因为 PIT 需要：

```text
dated membership snapshot
+ segment concepts
```

## 4. 当前结论

```text
2024 历史       ❌ 数据不存在
2025-2026 扩主题 ⏳ 需要 SEED + concept 映射
现有 dead 样本  6 episodes
```

## 5. 下一步

优先扩 2025-2026 的主题/segment，而不是伪造 2024：

- 先加 `光伏`、`储能`、`火电`、`水电`、`航空装备`；
- 每个主题必须有 concept 映射和 PIT 成员快照；
- 重建 labels 后再看 dead episode 是否 ≥30。

没有 2024 数据，就不应该用 2024 做“验证”。
