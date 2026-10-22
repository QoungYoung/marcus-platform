# dead episode 分析：反弹失败确认 + 相对收益

> 2026-10-22；只看 dead episode onset，不再按 dead 周行数统计。

## 定义

- 每个 `dead_episode` 取第一次 dead 的日期；
- T+1~T+3 若收盘收回 `last_high * 0.98`，判 `rebound`；
- 否则 `confirmed_dead`；
- 结果看 5 日 `second`、`second - leader`、`second` 最小值（尾部）。

## 结果

```text
dead episodes 可计算：6
confirmed_dead：6
rebound：0
```

| 指标 | 均值 | 最小值 | 胜率 |
|---|---:|---:|---:|
| second | +0.32% | -12.83% | 33% |
| high_cut_low | **-0.14%** | -13.07% | 50% |
| second 尾部 | +0.15%? | -12.83% | 33% |

> 尾部指标受单个 -12.8% 样本主导。

## 读法

1. 6 个 dead 全部没有在 3 日内收回 `last_high`，确认成立；
2. 高切低均值为 **-0.14%**，方向开始符合狼大；
3. 但 n=6，且有一个 -12.8% 的尾部，**不能算验证**；
4. dead 本身太稀有，93 个周度日期只有 6 个可算 episode。

## 结论

```text
dead 的“反弹失败确认”     ✅ 可做
dead 后高切低方向          ✅ 略负
dead 统计显著性            ❌ n=6
```

不是 dead 定义还差一个阈值，而是 **样本不够**。

## 下一步

- 扩 PIT 历史到 2024 年或更早；
- 或扩大主题/segment 宇宙；
- 或接受 dead 只能做定性提示，不做统计验收。
