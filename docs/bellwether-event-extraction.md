# 风向标事件提取 v0

> 2026-10-22；按 `bellwether-aligned-spec.md` 提取。
> 产物：`data/core_pit/bellwether_events.csv`

## 口径

- leader：PIT `rank=1`，按 `theme + segment_role`；
- impulse：leader high 放量创新高；
- alive：close >= impulse high * 0.98；
- warning：低于 impulse high，但未破失效线；
- dead：跌破 impulse low，或 low 缺失时跌破 impulse high * 0.90；
- second_catchup：
  - state=alive
  - leader 自 impulse 涨幅 >= 8%
  - second 滞后 >= 5pp
- high_cut_low：
  - state=alive
  - leader extended（5日 >=12% 或距 MA20 >=15%）
  - second 滞后 >= 5pp

## 结果

```text
日期 93
行数 3895
主题 15

alive    298
warning  777
dead    2659
unknown  161

second_catchup  4
high_cut_low    8
rotated       138
```

## 问题

1. `dead` 仍占 68%，说明 impulse/失效线定义偏严；
2. `second_catchup` 只有 4 个，**无法做动作回测**；
3. 根因：impulse 在每次新高都重置，`leader 自 impulse 涨幅`永远接近 0；
4. `rotated` 138 只是 rank1 代码变化，未验证“老龙头 dead + 新龙头接力”；
5. low 在部分历史日期用 close 补，dead 只能近似。

## 结论

事件提取器 v0 能跑，但**动作样本不足**，不能拿去验证补涨/高切低。

## 下一步

1. 把 impulse 拆成：
   - `cycle_start`（本轮第一次 impulse）
   - `last_impulse_high`（最新高点）
2. 补涨涨幅从 `cycle_start` 算，不再从最新 impulse 算；
3. rotated 必须满足：
   - 老 leader 进入 dead
   - 新 leader 有独立 impulse
4. 重新提取后再看事件数，n≥30 才做动作回测。
