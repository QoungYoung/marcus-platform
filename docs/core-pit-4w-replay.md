# core 动作闸：4 周 PIT 历史回放

> 2026-10-22；最近 4 个 PIT 周度日期：20260930、20261009、20261016、20261021。

## 结果

```text
总 picks: 40
allowed: 28
blocked: 12
```

按状态：

| 状态 | allow | block |
|---|---:|---:|
| alive | 6 | 0 |
| warning（leader） | 22 | 0 |
| warning（second） | 0 | 12 |

按阶段：

| stage | allow | block |
|---|---:|---:|
| strong | 6 | 12 |
| weak | 6 | 0 |
| downtrend | 16 | 0 |

## 5 日结果（仅早期日期有 forward）

| 组 | n | 5日均值 |
|---|---:|---:|
| allowed | 12 | -0.37% |
| blocked | 4 | **-5.54%** |

方向上 gate 挡掉的更差，但样本极小。

## 20 日结果

最近 4 周太新，**没有完整 20 日 forward**；只能看 5 日，且只有 4 个 blocked 样本。  
不能据此判断回撤/尾部。

## 合规

```text
dead + second = 0
warning + second = 0
```

通过。

## 结论

```text
动作闸合规   ✅
短窗方向     ⚠️ blocked 5日更差，但 n=4
20日风险     ❌ 样本不足
```

4 周 PIT 回放可以验证“动作闸有没有乱挡”，但**不足以验证 20 日风险**。  
下一步要么等 20 日 forward，要么回放更早的 4 周窗口。

## 产物

- `data/core_pit/replay_4w.csv`
- `data/core_pit/replay_4w.json`
- `jobs/replay_core_gate_4w.py`

---

## 更早的完整 4 周窗口（20260828 ~ 20260918）

为拿到完整 20 日 forward，回放更早的 4 周：

```text
总 picks: 97
allowed: 59
blocked: 38
```

状态：

| 状态 | allow | block |
|---|---:|---:|
| alive | 29 | 0 |
| rotated | 6 | 0 |
| warning | 14 | 34 |
| stale | 10 | 4 |

结果：

| horizon | allowed | blocked |
|---|---:|---:|
| 5日 | -1.98% (n59) | **-3.22% (n38)** |
| 10日 | **-4.53% (n59)** | -3.73% (n38) |
| 20日 | **-5.26% (n49)** | -4.28% (n24) |

结论：

- 5 日 gate 挡得更好；
- **10/20 日 blocked 反而更好** → gate 挡掉了一些后来反弹的票；
- 样本 24-38，仍小；
- 该窗口整体是下跌市，收益全负，不能以此判断绝对好坏；
- 不能证明 20 日风险改善。

> 数据说明：`bars_api` 缺失的日期用 `bars_combined` 补，high 暂用 close；这会影响状态精度。
