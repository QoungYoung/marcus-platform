# PointResolver 盲测 v5：加 structure_rank + 优先级后

> 输入仍是完整 `point_engine` 全量候选、无狼大原话/点位、4 个中性 case、候选顺序打乱。
> 本轮新增：
> - 候选补 `prior_day_low` / `filled_gap` / `state` / `structure_rank`
> - 选择规则：`structure_rank` 优先（3>2>1）；同 rank 看 `confluence`；再同看距离；**排除 rank=1 的纯缺口/当日高低边界**，除非没有 rank>=2。

## 1. 结果：4/4 命中第一档

| case | 当前点数 | agent risk_trigger | 他的第一档 | 偏差 |
|---|---:|---:|---:|---:|
| A | 3359.90 | **3372.40** (ma20) | 3370 | **+0.07%** |
| B | 3834.89 | **3922.58** (neckline/swing_low/measured) | 3922 | **+0.01%** |
| C | 3836.77 | **3922.58** | 3922 | **+0.01%** |
| D | 3878.00 | **3885.31** (neckline) | 3888 | **-0.07%** |

指标：

| 指标 | v4 | v5 |
|---|---:|---:|
| risk 在现价上方 | 4/4 | **4/4** |
| risk 命中第一档（±0.5%） | 1/4 | **4/4** |
| 命中第一档（±0.1%） | 1/4 | **4/4** |
| 编造候选外价格 | 0 | **0** |

## 2. 它这次选对了什么

- **B/C 不再选 3839/3900，而是回到 3922.58**：
  `3922.58` 同时是 `neckline + swing_low + measured_move`，structure_rank=3、confluence=12；
  `3839.97` 是 filled_gap_upper，rank=1，被规则排除。
- **D 不再选 3912，而是回到 3885.31**：
  `3885.31` 是 neckline，rank=3、confluence=18；`3912.01` 是 gap_lower，rank=1。
- **A 不再选 3388.78，而是 3372.40**：
  `3372.40` 是 ma20/boll_mid，rank=3、confluence=16；`3370.80` 是 today_high，rank=1。

## 3. 后续审计：v5 规则过拟合

见 [point-resolver-overfit-audit.md](point-resolver-overfit-audit.md)：

- `structure_rank` 硬优先级、`排除 rank=1`、`rank>confluence>distance` 都是看着 A-D 失败案例加的；
- 机械消融 6 个 case：`max_confluence` 5/6，`rank>confluence` 5/6，但失败点不同（C vs F）；
- **C 和 F 不能同时被一条硬规则满足**；
- **`排除 rank=1` 已删除**：它会把 F 的 3950.31 缺口排除，让 MA20 3979.7 选错。

## 4. 这轮不是“干净盲测”

- 输入是盲的（没有他的点位），但 **选择规则是看着 v4 的失败案例改的**：
  - 加 `structure_rank`；
  - 排除 rank=1 的纯缺口/当日高低；
  - rank > confluence > 距离。
- 所以 4/4 可能是**规则拟合**，不能直接当成“agent 已经会找他的点”。

## 5. 下一步：留出验证

拿 **R05（2025-08-28，3856）** 和 **R09（2025-11-18，3950）** 做 holdout：

- 这两条没有参与 v4→v5 的规则修改；
- 如果它们也能命中 3856/3950，才说明规则不是只对这 4 条有效；
- 如果命中不了，就说明 v5 是过拟合，要退回“结构规则 + 人工优先级”。

## 6. 一句话

**v5 把 4/4 拉回来了：A 3372.4、B/C 3922.58、D 3885.31，全部 ±0.1% 内。**
**但规则是看着失败案例改的，必须用 R05/R09 留出验证，不能直接宣布成功。**
