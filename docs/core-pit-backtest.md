# core PIT 回测 v0

> 2026-10-22；PIT chain_map 周频 + PIT 主题成员；20 日持有。

## 口径

- 日期：`data/pit/chain_map/labels_weekly.csv` 的 93 个周度日期；
- 可计算 forward 20d 的日期：87；
- 角色：PIT labels 的 `rank/is_second`；
- stage：用 PIT 主题成员等权 r5/r20 + breadth20 现算；
- 选股：
  - strong/extended：rank2-4/second；
  - weak/downtrend：rank1；
  - low_flat/oversold/unknown：不做；
- 每组每日等权，20 日后收益；按日期汇总。

## 对照

| 组 | 20日均值 | 胜率 | t |
|---|---:|---:|---:|
| A rule（stage×role） | **+1.458%** | 55.2% | 1.91 |
| B overlay（简化龙头状态乘数） | **+1.312%** | 57.5% | 1.72 |
| C theme_eq（PIT 成员等权） | **+1.051%** | 52.9% | 1.25 |

H1/H2：

| 组 | H1 | H2 |
|---|---:|---:|
| A | +2.858% (t2.81) | +0.025% (t0.02) |
| B | +2.520% (t2.50) | +0.076% (t0.07) |
| C | +2.369% (t2.76) | -0.297% (t-0.21) |

## 结论

1. A 规则相对 theme_eq 有 +0.41pp 的初步超额，t=1.91；
2. 但 **H2 几乎为零**，优势集中在 H1，不能算稳定；
3. B overlay 略低于 A，未证明 overlay 增益；
4. B 的龙头状态是简化代理（MA20/20日低），不是生产 v3，不能当生产 overlay 结论；
5. 2221 个 picks、87 个日期，样本重叠，t 只作描述；
6. PIT 主题成员只覆盖 32/49，2025-08 前无成员证据，存在选择偏差。

## 不能宣称

- 不能说 core 线已稳定 alpha；
- 不能说 overlay 有效；
- 不能说 H1/H2 都成立；
- 不能直接上仓位。

## 下一步

1. 用生产 v3 状态做 PIT 重放（不是 MA20 代理）；
2. 补 PIT 成员/chain_map 后重跑；
3. 块状 bootstrap / 分半稳健性；
4. 4 周影子验证，再谈仓位。
