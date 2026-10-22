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
