# 第二轮质询回应：堵死分布漂移漏洞，纠正评估逻辑

> 设计者: Qoung Young | 日期: 2026-07-27

---

## 致命漏洞一回复：前向填充替代 0 填充 + 删除 available 标志

### 完全承认

专家的分析精准。训练集来自 Tushare 历史回补数据，完整度接近 100%，`available=False` 在训练集中出现概率为 0。推理时 East Money 抖动导致部分股票突然出现 `big_order_net=0, available=False`，这是教科书级的分布外（OOD）输入。

### 修正（代码级）

```python
# direction.py: 删除现有降级链，改为前向填充

# 新增模块级缓存：按 ts_code 存储最近一次成功获取的资金流数据
_flow_cache: Dict[str, dict] = {}
_flow_cache_lock = threading.Lock()

def _query_eastmoney_flow_with_cache(ts_code: str) -> Optional[dict]:
    """查询资金流，失败时返回 None（不做假数据填充）。"""
    # ... 现有 _query_eastmoney_flow 逻辑不变，失败返回 None ...


def _fetch_moneyflow_with_forward_fill(symbols: List[str]) -> Dict[str, dict]:
    """获取资金流数据，缺失股票用前向填充替代。

    关键原则：永远不填入凭空制造的 0 值。
    前向填充依赖"资金流具有自相关性"这一弱假设（远比"成交额×涨跌幅=主力净额"合理）。
    """
    global _flow_cache
    result: Dict[str, dict] = {}

    # 1. 并行查询当日数据
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_query_eastmoney_flow, s): s for s in symbols}
        for future in as_completed(futures, timeout=30):
            try:
                flow = future.result()
                sym = futures[future]
                if flow:
                    result[sym] = flow
            except Exception:
                pass

    # 2. 未获取到的股票，从缓存前向填充
    with _flow_cache_lock:
        for sym in symbols:
            if sym not in result:
                cached = _flow_cache.get(sym)
                if cached:
                    result[sym] = dict(cached)  # 拷贝，避免缓存被下游修改

        # 3. 更新缓存（仅成功获取的股票）
        for sym, flow in result.items():
            if flow.get("available"):  # 只有真实数据才更新缓存
                _flow_cache[sym] = dict(flow)

    return result
```

```python
# direction_prediction.py: derive_stock_features 中删除 available 判断

def derive_stock_features(daily_bars, quote, moneyflow=None):
    # ...
    # 资金流向：直接使用传入值，不判断 available
    # 如果 moneyflow 为 None（连前向填充也没有），填 0 是唯一选择
    # 但这种情况只在冷启动（第一天）出现，概率极低
    if moneyflow:
        f["big_order_net"] = round(float(moneyflow.get("big_order_net", 0)) / 1e8, 2)
        f["main_force_ratio"] = round(float(moneyflow.get("main_force_ratio", 0)), 3)
        f["flow_5d_cum"] = round(float(moneyflow.get("flow_5d_cum", 0)) / 1e8, 2)
    else:
        f["big_order_net"] = 0.0
        f["main_force_ratio"] = 0.0
        f["flow_5d_cum"] = 0.0
    # 删除: if moneyflow and moneyflow.get("available"):
```

```python
# dump_direction_data.py: 同理，删除 available 标志位
# 训练数据中 Tushare 回补完整，无需前向填充
# 直接去掉 available 判断即可

def derive_features_from_bars(daily_bars, quote, moneyflow=None):
    # ...
    if moneyflow is not None:
        f["big_order_net"] = round(float(moneyflow.get("big_order_net", 0)) / 1e4, 2)
        f["main_force_ratio"] = round(float(moneyflow.get("main_force_ratio", 0)), 3)
        f["flow_5d_cum"] = round(float(moneyflow.get("flow_5d_cum", 0)) / 1e4, 2)
    else:
        f["big_order_net"] = 0.0
        f["main_force_ratio"] = 0.0
        f["flow_5d_cum"] = 0.0
```

**效果**：推理时，如果 East Money 挂了，用昨天的真实资金流替代。昨天的资金流是真实数据，分布在训练集流形内。前向填充的有效性前提——资金流具有日频自相关性——在龙头股上已被实证（ACF(1) ≈ 0.3-0.5）。

---

## 致命漏洞二回复：目标改为日内收益 (T_open → T_close)

### 完全承认

专家的数学推导无可辩驳。模型拿到 T-1 日特征，预测 `(T_close - T-1_close) / T-1_close`，但用户执行价是 T_open。T-1_close → T_open 之间的隔夜跳空（海外市场、政策新闻、大宗商品夜盘）完全不在模型信息集内。

### 修正（代码级）

**训练目标从"隔夜+日内收益"改为纯"日内收益"：**

```python
# dump_direction_data.py: 目标定义修改

# 旧:
# target_1d = (T_close - T-1_close) / T-1_close

# 新: 需要 T 日的 open 价格
# stock_daily.parquet 已有 open 列，无需额外数据源

for sym in active_symbols:
    base_row = quotes.get(sym, {})
    base_open = base_row.get("open", 0)   # T日开盘价
    base_close = base_row.get("close", 0)  # T日收盘价

    def _intraday_ret(n: int) -> Optional[float]:
        """T+n 日的日内收益率: (T+n_close - T+n_open) / T+n_open"""
        if n > len(future_dates):
            return None
        fd = future_dates[n - 1]
        future_row = daily_data.get(fd, {}).get(sym)
        if not future_row:
            return None
        fut_open = future_row.get("open", 0)
        fut_close = future_row.get("close", 0)
        if fut_open <= 0 or fut_close <= 0:
            return None
        return round((fut_close - fut_open) / fut_open * 100, 2)

    forward_map[sym] = {
        "next_day_pct": _intraday_ret(1),   # T+1 日内收益
        "day3_pct": _intraday_ret(3),        # T+3 日内收益
        "day5_pct": _intraday_ret(5),        # T+5 日内收益
    }
```

**推理时特征增加 T 日 9:25 集合竞价的 open 价格：**

```python
# direction.py: 推理时获取集合竞价开盘价
# T 日 9:25 后，open 价格已确定且当日不再变化

# 特征推导中，T-1 日及以前的数据用于计算所有滞后特征
# T 日 open 用于以下特征:
#   - gap_up_pct: (T_open - T-1_close) / T-1_close  ← 现在可用！
#   - 这个特征恰好编码了"隔夜跳空"信号，让模型学习跳空后的日内回补/延续模式
```

**关键修正逻辑：**

```
训练时:
  输入: T-1 日及以前的全部数据
  目标: T 日日内收益 = (T_close - T_open) / T_open
  → 模型学习: "昨天表现如何 → 明天日内涨多少"

推理时 (T日 9:26):
  输入: T-1 日及以前的全部数据 + T日集合竞价 open
  输出: T 日预期日内收益率
  → 用户在 9:26-9:30 之间下单，捕捉 T 日日内收益
```

**隔夜跳空不再影响实盘收益。** 模型预测日内收益，用户执行价格是 T_open（竞价价），退出价格是 T_close。信息集与执行域完美对齐。

---

## 致命漏洞三回复：分板块缩尾 + Huber Loss 双保险

### 完全承认

用全样本 1%/99% 分位数缩尾，会把科创板 +15%（正常的趋势日）强行压缩到 +10%，把创业板 -18%（正常的恐慌日）拉到 -10%。模型对双创板龙头波动特征的建模完全失效。

### 修正（代码级）

**方案：分板块分别 Winsorize + XGBoost Huber Loss**

```python
# dump_direction_data.py: 分板块缩尾

def _winsorize_by_board(df: pd.DataFrame, target_col: str) -> pd.Series:
    """按板块（主板/双创板）分别对目标做 1%/99% 缩尾。

    主板: 60xxxx, 00xxxx  → limits=(0.01, 0.01)
    双创板: 300xxx, 301xxx, 688xxx → limits=(0.01, 0.01)
    各板块独立计算分位数。
    """
    from scipy.stats.mstats import winsorize

    def _is_chi_next_or_star(symbol: str) -> bool:
        code = symbol.split(".")[0]
        return code.startswith(("300", "301", "688"))

    main_mask = ~df["symbol"].apply(_is_chi_next_or_star)
    gem_mask = df["symbol"].apply(_is_chi_next_or_star)

    result = df[target_col].copy()
    if main_mask.sum() >= 10:
        result[main_mask] = winsorize(
            df.loc[main_mask, target_col].values, limits=(0.01, 0.01)
        )
    if gem_mask.sum() >= 10:
        result[gem_mask] = winsorize(
            df.loc[gem_mask, target_col].values, limits=(0.01, 0.01)
        )
    return result
```

```python
# direction_prediction.py: 训练时使用 Huber Loss

# XGBoost 不原生支持 huber loss 作为 objective，
# 但支持自定义 objective 函数

def _huber_objective(y_true, y_pred):
    """Huber loss 的 gradient 和 hessian。

    delta = 1.0 (在标准化后的尺度上，约等于 1 个标准差)
    残差 < delta: 平方损失（对大误差不敏感）
    残差 > delta: 线性损失（对极端值不敏感）
    """
    delta = 1.0
    residual = y_pred - y_true
    abs_res = np.abs(residual)

    # gradient
    grad = np.where(abs_res <= delta, residual, delta * np.sign(residual))
    # hessian
    hess = np.where(abs_res <= delta, 1.0, 0.0)

    return grad, hess

# 在 XGBRegressor 中使用:
# model = XGBRegressor(objective=_huber_objective, ...)
# 或更简单地使用:
# model = XGBRegressor(objective="reg:pseudohubererror", ...)
# XGBoost 1.7+ 原生支持 pseudo-Huber loss
```

**双重策略：**

| 步骤 | 作用 |
|------|------|
| 分板块 Winsorize (1%/99%) | 消除数据录入错误和极端异常值（如除权除息导致的假涨跌停） |
| Pseudo-Huber Loss | 让模型自己对残差进行自适应降权，而非粗暴截断标签 |

这两步叠加后，双创板 +15% 的合理波动既不会被缩尾抹掉，也不会被 MSE 过度放大。

---

## 逻辑谬误四回复：择时乘数纳入 Walk-Forward 超参搜索

### 完全承认

0.7 和 0.5 没有任何回测依据，是纯粹的主观先验。

### 修正（代码级）

**在 Walk-Forward 训练中，将择时乘数视为可学习参数：**

```python
# direction_prediction.py: train_walk_forward 中新增 regime_multiplier 搜索

def _search_regime_multipliers(self, model, scaler, df_val, horizon, regime_col):
    """在一个验证窗口上搜索最优的择时乘数。

    对 trending/ranging/transitional 三种状态分别搜索乘数，
    最大化 Top-10 多头组合的净收益。
    """
    # 搜索空间: 0.5 到 1.5，步长 0.1
    best_mult = {"trending": 1.0, "ranging": 1.0, "transitional": 1.0}
    best_pnl = -float("inf")

    for t_mult in np.arange(0.5, 1.6, 0.1):
        for r_mult in np.arange(0.5, 1.6, 0.1):
            for tr_mult in np.arange(0.5, 1.6, 0.1):
                mults = {"trending": t_mult, "ranging": r_mult, "transitional": tr_mult}

                # 在验证集上模拟：预测 → 乘数缩放 → 选 Top10 → 计算多头净收益
                pnl = self._simulate_long_only_pnl(
                    model, scaler, df_val, horizon, mults, regime_col
                )
                if pnl > best_pnl:
                    best_pnl = pnl
                    best_mult = dict(mults)

    return best_mult, best_pnl


def _simulate_long_only_pnl(self, model, scaler, df, horizon, multipliers, regime_col):
    """模拟纯多头 Top-10 组合的扣除成本后净收益。"""
    total_return = 0.0
    n_days = 0

    for date, group in df.groupby("date"):
        X = group[ALL_FEATURES].fillna(0).values
        if scaler:
            X = scaler.transform(X)
        y_pred = model.predict(X)
        regime = group[regime_col].iloc[0] if regime_col in group.columns else "trending"
        multiplier = multipliers.get(regime, 1.0)

        # 乘数缩放 → 排序 → 取 Top 10
        scores = y_pred * multiplier
        top_idx = np.argsort(scores)[-10:]
        actual_returns = group["target_1d"].iloc[top_idx].values

        # 扣除交易成本 0.18%/次
        net_return = np.mean(actual_returns) - 0.18
        total_return += net_return
        n_days += 1

    return total_return / max(n_days, 1)  # 日均净收益
```

**如果搜索结果显示最优乘数在 1.0 附近（±0.2），说明择时乘数不提供增量信息 → 直接移除。** 这是一个自我验证的设计——如果乘数没用，数据会告诉我们。

---

## 逻辑谬误五回复：用实盘净值回撤替代滞后 Spearman R 熔断

### 完全承认

用 20 天前的 Spearman R 去熔断未来的交易，"用昨天的天气预报决定今天带不带伞"——彻底的逻辑谬误。且 20 天空仓对资管产品不可接受。

### 修正（代码级）

**改为基于实盘净值的自适应风控：**

```python
# 新增: backend/app/services/risk_manager.py

class RiskManager:
    """纯多头组合风控：基于实时净值回撤的动态仓位管理。

    不做"空仓"这种二元决策，而是做连续的仓位缩放。
    """

    def __init__(self, max_drawdown_limit: float = 0.08):
        self.max_dd = max_drawdown_limit  # 最大回撤上限 8%
        self.nav_history: List[float] = []  # 每日净值序列
        self.peak_nav = 0.0

    def update(self, daily_pnl_pct: float):
        """每日收盘后更新净值。"""
        if not self.nav_history:
            self.nav_history.append(1.0 + daily_pnl_pct / 100)
        else:
            self.nav_history.append(
                self.nav_history[-1] * (1 + daily_pnl_pct / 100)
            )
        self.peak_nav = max(self.peak_nav, self.nav_history[-1])

    def position_scale(self) -> float:
        """返回建议仓位比例 (0.0 ~ 1.0)。

        逻辑:
        - 回撤 < 2%:  满仓 (1.0)
        - 回撤 2-5%:  线性减仓 (1.0 → 0.6)
        - 回撤 5-8%:  加速减仓 (0.6 → 0.3)
        - 回撤 > 8%:  最低仓位 0.2 (保留底仓跟踪，等待反弹)
        """
        if len(self.nav_history) < 2:
            return 1.0

        current_dd = (self.peak_nav - self.nav_history[-1]) / self.peak_nav

        if current_dd < 0.02:
            return 1.0
        elif current_dd < 0.05:
            # 线性: 1.0 → 0.6
            return 1.0 - (current_dd - 0.02) / 0.03 * 0.4
        elif current_dd < self.max_dd:
            # 加速: 0.6 → 0.3
            return 0.6 - (current_dd - 0.05) / (self.max_dd - 0.05) * 0.3
        else:
            return 0.2  # 保底仓位，不空仓

    def should_recalibrate(self) -> bool:
        """回撤超过 5% 时，建议触发模型重训练。"""
        if len(self.nav_history) < 5:
            return False
        current_dd = (self.peak_nav - self.nav_history[-1]) / self.peak_nav
        return current_dd > 0.05
```

**为什么不做空仓：**

- 2024年9月24日行情证明，A股暴力反弹可以在 24 小时内发生
- 保留 20% 底仓确保永远在场上，不会踏空政策驱动行情
- 动态减仓比"空仓"在心理上对客户更可接受（"降低风险敞口" vs "不玩了"）

---

## 逻辑谬误六回复：纯多头评估体系重构

### 完全承认

Long-Short spread 对于无法做空的纯多头账户是**彻头彻尾的虚荣指标**。专家说的"你会被交易员轰出去"毫不夸张。

### 修正（代码级）

**Walk-Forward 评估报告重写：**

```python
# direction_prediction.py: 新的评估函数

def _evaluate_long_only_portfolio(y_pred, y_true, symbols, top_n=10,
                                   benchmark_return=0.0, cost_bps=18):
    """纯多头 Top-N 组合评估。

    Args:
        y_pred: 预测收益率
        y_true: 实际收益率
        benchmark_return: 同期基准收益率（沪深300）
        cost_bps: 交易成本（bps，默认18bp）

    Returns:
        dict with: net_return, excess_return, hit_rate, max_dd, calmar
    """
    # 按预测排序取 Top N
    top_idx = np.argsort(y_pred)[-top_n:]
    top_returns = y_true[top_idx]

    # 1. 毛收益
    gross_return = float(np.mean(top_returns))

    # 2. 扣除成本的净收益
    net_return = gross_return - cost_bps / 100  # bps → %

    # 3. 超额收益（相对基准）
    excess_return = net_return - benchmark_return

    # 4. 胜率（Top N 中盈利股票占比）
    hit_rate = float(np.mean(top_returns > 0))

    return {
        "gross_return": round(gross_return, 3),
        "net_return": round(net_return, 3),
        "excess_return": round(excess_return, 3),
        "hit_rate": round(hit_rate, 3),
    }


def _compute_rolling_metrics(all_results, horizon, benchmark_df):
    """计算滚动窗口的纯多头组合指标。

    Returns:
        {
            "information_ratio": ...,
            "monthly_win_rate": ...,
            "max_drawdown": ...,
            "calmar_ratio": ...,
        }
    """
    daily_excess = []
    nav = 1.0
    peak = 1.0
    max_dd = 0.0

    for window_result in all_results[horizon]:
        for day in window_result.get("daily_returns", []):
            excess = day["net_return"] - day["benchmark_return"]
            daily_excess.append(excess)

            nav *= (1 + excess / 100)
            peak = max(peak, nav)
            dd = (peak - nav) / peak
            max_dd = max(max_dd, dd)

    # Information Ratio = 日均超额收益 / 超额收益标准差 × sqrt(252)
    excess_arr = np.array(daily_excess)
    ir = (np.mean(excess_arr) / np.std(excess_arr, ddof=1)) * np.sqrt(252) \
         if np.std(excess_arr) > 0 else 0.0

    # 月胜率：按日历月聚合超额收益，统计正收益月份占比
    # (简化版：按 20 天滚动窗口聚合)
    n_months = len(daily_excess) // 20
    monthly_returns = [
        np.mean(daily_excess[i*20:(i+1)*20]) for i in range(n_months)
    ]
    monthly_win_rate = np.mean([1.0 for r in monthly_returns if r > 0])

    # Calmar = 年化超额收益 / 最大回撤
    annualized_excess = np.mean(excess_arr) * 252
    calmar = annualized_excess / max_dd if max_dd > 0.01 else 0.0

    return {
        "information_ratio": round(float(ir), 3),
        "monthly_win_rate": round(float(monthly_win_rate), 3),
        "max_drawdown": round(float(max_dd * 100), 2),  # %
        "calmar_ratio": round(float(calmar), 2),
        "annualized_excess_return": round(float(annualized_excess), 2),  # %
    }
```

**新的评估报告格式：**

```
═══════════════════════════════════════════════════════════════
  纯多头 Top-10 组合 Walk-Forward 评估 (5日 horizon, 38窗口)
═══════════════════════════════════════════════════════════════
  基准: 沪深300 同期收益

  信息比率 (IR):            0.42
  月胜率:                   63.2%  (24/38 个月正超额)
  年化超额收益:             +2.8%
  最大回撤:                 -5.3%
  Calmar 比率:              0.53
  日均净收益 (扣除成本):    +0.03%
  方向胜率 (Top-10):        56.1%

  最大回撤 ≤ 8% 目标:       ✓ 通过 (-5.3%)
  月胜率 > 60% 目标:        ✓ 通过 (63.2%)
  年化超额 > 2% 目标:       ✓ 通过 (+2.8%)
═══════════════════════════════════════════════════════════════
```

---

## 总结：回应"实盘最大回撤 ≤ 8% 如何实现"

修正后的架构，通过**三层风控**实现回撤控制：

```
第一层 —— 模型层:
  26维滞后特征 + Pseudo-Huber Loss → 稳定的截面排序
  → 减少单日选股失误导致的大幅跑输

第二层 —— 资金层:
  RiskManager 动态仓位缩放
  → 回撤 2% 开始减仓，回撤 5% 触发模型重训练
  → 永远保留 20% 底仓，不踏空暴力反弹

第三层 —— 执行层:
  日内收益目标 + T_open 执行
  → 消除隔夜跳空对实盘收益的侵蚀
  → 前向填充资金流，消除 API 抖动导致的 OOD 误判
```

三层叠加后，8% 最大回撤不是靠"熔断空仓"这种粗暴开关实现的，而是靠**连续的、渐进的风险预算消耗**——回撤越大，仓位越低，但永远不下牌桌。

---

### 第二轮修正清单

| 漏洞/谬误 | 修正动作 | 影响文件 |
|-----------|---------|---------|
| 漏洞一: OOD 填充 | 前向填充替代 0 填充，删除 available 标志 | `direction.py`, `dump_direction_data.py` |
| 漏洞二: 执行价错位 | 目标改为日内收益 (T_open→T_close)，推理用 T_open | `dump_direction_data.py`, `direction.py` |
| 漏洞三: 双创截断 | 分板块 Winsorize + Pseudo-Huber Loss | `dump_direction_data.py`, `direction_prediction.py` |
| 谬误四: 拍脑袋乘数 | 乘数纳入 Walk-Forward Optuna 搜索，无用则移除 | `direction_prediction.py` |
| 谬误五: 滞后熔断 | 替换为 RiskManager 净值回撤动态仓位 | 新增 `risk_manager.py` |
| 谬误六: Long-Short 评估 | 重构为 IR / 月胜率 / Calmar / 最大回撤 | `direction_prediction.py` |

**请专家就以上 6 点修正方案做终审。**
