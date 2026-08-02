# 第三轮修正：持有期收益目标 + 三个操作性陷阱

> 设计者: Qoung Young | 日期: 2026-07-27

---

## 致命架构错误回复：目标变量从"单日脉冲"改为"持有期收益"

### 完全承认——这是降维打击级别的错误

专家的算术推演无可辩驳。我举一个更直观的等价表述：

**第二轮修正后的模型在回答什么问题？**

> "如果我在第 T+N 天开盘买入、收盘卖出，这一天能赚多少？"

**实盘账户需要模型回答什么问题？**

> "如果我今天（T日）开盘买入、持有 N 天后（T+N日）收盘卖出，总共能赚多少？"

这两个问题的答案在 N=1 时相同（T+1日开盘≈T日开盘的次日），但在 N>1 时完全脱钩。第二轮修正把模型变成了一个**单日脉冲探测器**——它能找到"周五日内反弹 +2%"的股票，但找不到"周一开盘到周五收盘涨了 12%"的股票。

### 修正

回退目标定义，改为正确的持有期收益：

```python
# dump_direction_data.py: 目标定义——第三轮修正（最终版）

# 训练数据时间线:
#   T-1 日: 特征数据截止日（所有特征基于 T-1 及历史）
#   T 日:   执行日（用户以 T_open 买入）
#   T+N 日: 平仓日（用户以 T+N_close 卖出）
#
# 目标: (T+N_close - T_open) / T_open × 100
#
# 隔夜跳空不剔除——它就是持有期收益的组成部分，
# 模型需要学会基于 T-1 日数据预测跳空+日内+趋势的总和。

for sym in active_symbols:
    base_row = quotes.get(sym, {})          # T 日行情（推理时 9:25 可得）
    base_open = base_row.get("open", 0)      # T 日开盘价

    if base_open <= 0:
        forward_map[sym] = {target: None for target in ["next_day_pct", "day3_pct", "day5_pct"]}
        continue

    def _holding_return(n: int) -> Optional[float]:
        """持有期收益: (T+n_close - T_open) / T_open × 100"""
        if n > len(future_dates):
            return None
        fd = future_dates[n - 1]  # T+n 日的日期
        future_row = daily_data.get(fd, {}).get(sym)
        if not future_row:
            return None
        future_close = future_row.get("close", 0)
        if future_close <= 0:
            return None
        return round((future_close - base_open) / base_open * 100, 2)

    forward_map[sym] = {
        "next_day_pct": _holding_return(1),   # 持有1日: (T+1_close - T_open) / T_open
        "day3_pct":     _holding_return(3),   # 持有3日: (T+3_close - T_open) / T_open
        "day5_pct":     _holding_return(5),   # 持有5日: (T+5_close - T_open) / T_open
    }
```

### 目标变量的数学验证

用专家的例子验算（N=5）：

```
T 日开盘价 (T_open)     = 100 元
T+5 日收盘价 (T+5_close) = 91.8 元

target_5d = (91.8 - 100) / 100 × 100 = -8.2%  ← 正确
```

模型预测 -8.2% → 不推荐买入 → 用户避开这次亏损。

**修正前模型预测 +2%（周五日内反弹）→ 推荐买入 → 用户亏损 -8.2%。**

### 特征与目标的时序对齐验证

```
训练时 (以 T=2026-07-27 为例):

  特征输入:
    T-1 = 2026-07-24 的所有数据（滞后1日）
    ├── change_pct:     07-24 的涨跌幅
    ├── vol_ratio_1d:   07-24 的量比
    ├── big_order_net:  07-24 的主力净流入
    ├── ret_5d:         07-17 → 07-24 的 5 日收益
    ├── rsi14:          截至 07-24 的 RSI
    └── ...

  预测目标:
    target_1d = (07-28_close - 07-27_open) / 07-27_open  ← 1日持有
    target_5d = (08-01_close - 07-27_open) / 07-27_open  ← 5日持有

  ✓ 特征全部在 07-24 收盘后可得
  ✓ 目标依赖的 07-27_open 是未来的执行价，不在特征信息集内
  ✓ 模型学习的正是"用昨天数据预测明天买入、持有N天的总收益"

推理时 (T=2026-07-28, 9:26):

  特征输入: 2026-07-25 的全部数据 (T-1)
  预测输出: target_5d = 预期 (08-02_close - 07-28_open) / 07-28_open

  ✓ 07-28_open 在 9:25 已确定
  ✓ 用户 9:26-9:30 下单
  ✓ 没有任何未来函数
```

### 隔夜跳空不再被"掩耳盗铃"

持有期收益目标天然包含隔夜跳空：

```
target_5d = (T+5_close - T_open) / T_open

分解:
  = (T+5_close - T+5_open) / T_open     ← T+5日日内收益分量（占比小）
  + (T+5_open - T+4_close) / T_open     ← T+5日隔夜跳空分量
  + (T+4_close - T+4_open) / T_open     ← T+4日日内分量
  + ...
  + (T+1_open - T_close) / T_open       ← T+1日隔夜跳空
  + (T_close - T_open) / T_open         ← T 日日内分量
```

隔夜跳空占 A 股收益的约 70%——它是收益的**核心组成部分**而非噪声。把它纳入目标让模型去学习预测，才是正解。

---

## 陷阱二回复：彻底放弃乘数搜索，固定为 1.0

### 完全承认

在验证集上搜索乘数是教科书级的数据泄露。专家的判断准确——这比拍脑袋 0.7 更危险，因为它带着"数据验证过"的虚假信心。

### 修正

```python
# direction_prediction.py: 删除 _search_regime_multipliers 和 _simulate_long_only_pnl

# 择时模块降级为纯信息提示，不做任何数值缩放:
# API 返回 market_regime 字段仅供参考，不乘到 expected_return 上

# 如果未来要做择时叠加，必须满足两个前提:
#   1. 使用完全独立的时序模型（不共享训练数据）
#   2. 乘数通过纯样本外期间的回测确定（例如用 2019-2023 训练，2024-2025 确定乘数）
```

**XGBoost 的截面排序独立决策，不做任何外部缩放。** `expected_return` 字段就是模型的最佳估计，不乘任何系数。

---

## 陷阱三回复：前向填充加 TTL + 中位数兜底

### 完全承认

连挂 3 天时，用第 1 天的资金流配第 3 天的量价，是特征维度间的时空错配。

### 修正

```python
# direction.py: 前向填充加入 TTL 和中位数兜底

_flow_cache: Dict[str, dict] = {}       # ts_code → {flow_data, timestamp}
_flow_cache_lock = threading.Lock()
_FLOW_CACHE_TTL = 1                      # 缓存有效期: 1 个交易日
_flow_history: Dict[str, list] = {}      # ts_code → 最近 20 日资金流值列表


def _fetch_moneyflow_with_forward_fill(symbols: List[str]) -> Dict[str, dict]:
    global _flow_cache, _flow_history
    result: Dict[str, dict] = {}
    today = datetime.now().strftime("%Y%m%d")

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

    with _flow_cache_lock:
        # 2. 更新缓存和历史
        for sym, flow in result.items():
            _flow_cache[sym] = {"data": dict(flow), "date": today}
            # 维护最近 20 日列表
            if sym not in _flow_history:
                _flow_history[sym] = []
            _flow_history[sym].append(flow.get("big_order_net", 0))
            if len(_flow_history[sym]) > 20:
                _flow_history[sym] = _flow_history[sym][-20:]

        # 3. 缺失股票的处理
        for sym in symbols:
            if sym in result:
                continue

            cached = _flow_cache.get(sym)
            if cached and _is_same_trading_day(cached["date"], today):
                # TTL 内: 用昨天数据
                result[sym] = dict(cached["data"])
            else:
                # 超 TTL: 用最近 20 日中位数
                hist = _flow_history.get(sym, [])
                median_val = float(np.median(hist)) if hist else 0.0
                result[sym] = {
                    "big_order_net": median_val,
                    "main_force_ratio": 0.0,
                    "flow_5d_cum": 0.0,
                }
    return result
```

**为什么中位数比 0 好、比过期缓存好：**
- 0 值在训练集中几乎不存在（龙头股无交易日资金流为 0）→ OOD
- 3 天前的缓存与当日量价特征不匹配 → 内部矛盾
- 20 日中位数是训练集最常见的值范围 → 落在分布密度最高处，模型对它的行为最稳定

---

## 陷阱四回复：固定周期重训替代回撤触发重训

### 完全承认

回撤触发重训在实盘中有三重不可行：训练耗时长、暴跌期重训学到恐慌、连续触发压垮服务器。

### 修正

```python
# risk_manager.py: 删除 should_recalibrate 方法
# direction_prediction.py: 新增固定周期重训

# 模型重训练策略:
#   频率: 每月最后一个交易日收盘后
#   数据: 最近 250 个交易日（约 1 年）
#   窗口: train_days=120, step=10
#   超参: 复用首次训练的最优参数（不再重新 Optuna 搜索）
#   耗时: < 5 分钟（无 Optuna，仅 walk-forward 最后一轮 fit）
#
# 风控独立于模型更新:
#   RiskManager 只做仓位缩放
#   模型只在日历周期更新
#   两者解耦

class ModelUpdateScheduler:
    """固定日历周期模型重训练调度器。"""

    def __init__(self, frequency: str = "monthly"):
        self.frequency = frequency  # "monthly" | "weekly"
        self.last_train_date: Optional[str] = None

    def should_retrain(self, today: str) -> bool:
        """判断今天是否需要重训练。

        Args:
            today: YYYYMMDD 格式日期
        """
        if self.last_train_date is None:
            return True

        if self.frequency == "monthly":
            # 每月最后一个交易日
            return self._is_month_end(today) and today > self.last_train_date
        elif self.frequency == "weekly":
            # 每周五
            return self._is_friday(today) and today > self.last_train_date
        return False

    def mark_trained(self, date: str):
        self.last_train_date = date
```

**风控与模型更新完全解耦：**

```
RiskManager:
  ├── 每日收盘后更新净值
  ├── 计算当前回撤
  ├── 输出建议仓位比例 (1.0 → 0.2)
  └── 不做任何模型更新

ModelUpdateScheduler:
  ├── 每月最后一个交易日触发
  ├── 用最近 250 天数据重新 walk-forward
  ├── 无 Optuna（复用超参）
  └── 保存新模型
```

---

## 终版修正汇总

| 轮次 | 问题 | 终版方案 |
|------|------|---------|
| R1 一 | 特征前瞻 | 全部特征滞后至 T-1 日 |
| R1 二 | 常数列特征 | 移除 9 维市场级特征 (35→26维) |
| R1 三 | 回归截断 | 分板块 Winsorize + Pseudo-Huber Loss |
| R1 四 | 资金流降级 | 删除"成交额×涨跌幅"代理 |
| R1 五 | 窗口长度 | 60/120/180/250 敏感性分析 |
| R1 六 | 交易成本 | Walk-Forward 增加净收益指标 |
| R2 一 | 资金流 OOD | 前向填充 + TTL + 20日中位数兜底 |
| R2 二 | 执行价错位 | → **被 R3 致命错误覆盖** |
| R2 三 | 双创截断 | 分板块缩尾 (已纳入 R1 三) |
| R2 四 | 择时乘数 | → **被 R3 陷阱二覆盖** |
| R2 五 | 滞后熔断 | RiskManager 净值回撤动态仓位 |
| R2 六 | Long-Short 评估 | IR / 月胜率 / Calmar / 最大回撤 |
| **R3** | **目标错配** | **目标改为持有期收益 (T+N_close - T_open) / T_open** |
| **R3** | **乘数过拟合** | **彻底放弃乘数，XGBoost 独立决策** |
| **R3** | **缓存老化** | **TTL=1天，超时用 20 日中位数兜底** |
| **R3** | **回撤重训** | **改为固定日历周期重训（每月），与风控解耦** |

---

## 最终架构图

```
┌──────────────────────────────────────────────────────────────┐
│  每日推理 (T日 15:01)                                         │
│                                                              │
│  T-1 日全部量价特征 (26维，已滞后)                              │
│  + T-1 日资金流 (或 TTL 内前向填充 / 20日中位数)                │
│       │                                                      │
│       ↓                                                      │
│  XGBRegressor (Pseudo-Huber Loss)                             │
│       │                                                      │
│       ↓                                                      │
│  expected_return = E[(T+N_close - T_open) / T_open]          │
│       │                                                      │
│       ↓                                                      │
│  按 expected_return 降序 + 每行业≤2只                          │
│       │                                                      │
│       ↓                                                      │
│  T+1 日 9:26 出信号 → 用户竞价/开盘买入                        │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  每日风控 (T日 15:30)                                         │
│                                                              │
│  RiskManager.update(当日组合收益)                              │
│       │                                                      │
│       ↓                                                      │
│  当前回撤 → position_scale (1.0 ~ 0.2)                       │
│  绝不空仓，保留 20% 底仓                                       │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  每月重训练 (月末最后一个交易日 16:00)                          │
│                                                              │
│  ModelUpdateScheduler 触发                                    │
│  最近 250 天数据 → walk-forward (120d窗口, step=10)            │
│  复用首次 Optuna 超参，不重新搜索                               │
│  保存新模型 → direction_model.pkl                             │
└──────────────────────────────────────────────────────────────┘
```

**请专家终审。如果目标定义通过，立即按此架构重写全部代码。**
