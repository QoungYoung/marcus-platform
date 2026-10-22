# 风向标动作级回测（5日）

> 2026-10-22；91 个 PIT 日期，1,353 个 theme-day。  
> 不选股，只验证“二线/补涨/高切低”动作。

## 定义

对每个 PIT 日期 + 主题：

- `second`：PIT `bucket=second` 的等权 5 日收益；
- `leader`：PIT `rank=1` 的等权 5 日收益；
- `high_cut_low`：`second - leader`，即卖高买低的 5 日差；
- 按当时 `bellwether_state` 分组。

## 结果

| 状态 | second | leader | high_cut_low |
|---|---:|---:|---:|
| **alive** | **+0.782%**（t2.88, n368） | +0.457% | **+0.325%** |
| rotated | -0.005% | +0.531% | **-0.536%**（t-1.59） |
| stale | +0.495%（t1.59） | +0.046% | +0.449%（t1.90） |
| warning | -0.016% | -0.074% | +0.058% |
| dead | +4.50%（n2） | +3.55% | +0.95%（n2） |

## 读法

1. **alive 下做二线/补涨，5 日最好**：+0.782%，t2.88；
2. **rotated 下高切低最差**：-0.536%，t-1.59，说明换人时别卖高买低；
3. warning 下二线没有超额（-0.016%），但也没明显负；
4. stale 反而正，说明 stale 不应按 warning 处理；
5. dead 只有 2 个样本，不可用。

## 结论

```text
alive -> 允许二线/补涨      ✅ 有初步支持
rotated -> 禁止高切低       ✅ 有初步支持
warning -> 不做二线         ⚠️ 无超额，但非负
stale -> 不按 warning 挡    ✅ 结果支持
dead -> 停做                ❌ 样本不足
```

这是**动作层**证据，不是选股因子证据。  
仍受 PIT 固定龙头、5 日重叠、覆盖限制。

## 产物

- `data/core_pit/actions_5d.csv`
- `data/core_pit/actions_5d.json`
- `jobs/backtest_bellwether_actions.py`
