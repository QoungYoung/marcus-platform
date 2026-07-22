## 1. P0a: 规则 0b 成本止损振幅动态化（三档全扩宽）

- [x] 1.1 修改 `_check_cost_stop` "从未盈利"分支：`-4%` → `max(-4%, amplitude × 0.40)`
- [x] 1.2 修改 `_check_cost_stop` "小盈转亏"分支：`-3%` → `max(-3%, amplitude × 0.40)`
- [x] 1.3 修改 `_check_cost_stop` "无 HWM"分支：`-6%` → `max(-6%, amplitude × 0.50)`
- [x] 1.4 同步修改 `_calc_cost_stop_distance` 中的对应三档距离计算逻辑

## 2. P0b: 60 分钟趋势校验（执行层拦截）

- [x] 2.1 添加 `_check_60min_trend_uptrend(symbol)` 方法：调用 `get_intraday_min(freq='60min')` 获取 60 分钟 K 线，计算 MA10 和 MA30，返回 True 当 MA10 > MA30（上升趋势仍成立）
- [x] 2.2 添加 `_panic_suspension_count` 字典到 `__init__`，在 `_daily_reset` 中每日清零
- [x] 2.3 修改 `_execute_stop`：早盘冷静期检查之后、去重检查之前，调用 `_check_60min_trend_uptrend`。若上升趋势成立且挂起次数 < 3，跳过执行并递增计数，日志标记"恐慌错杀警戒"
- [x] 2.4 添加 3 秒超时 wrapper：超时或异常时跳过校验，直接执行止损

## 3. P0c: 规则 1 板块背离极端行情阈值动态化

- [x] 3.1 添加 `_is_extreme_panic()` 方法：调用 `get_moneyflow_mkt()` 获取全市场主力净流入，返回 True 当净流出 > 300 亿
- [x] 3.2 添加 `_get_stock_main_net(symbol)` 方法：调用 `get_stock_moneyflow(symbol)` 获取个股主力净流入（转换为亿元），2 秒超时，失败返回 None
- [x] 3.3 添加 `_get_sector_main_net(symbol)` 方法：通过 strategy_chain 的 latest_scan 获取板块名，再从 `get_concept_fund_flow` 或 sector_data 中获取板块主力净流入（亿元）
- [x] 3.4 添加 `_get_panic_divergence_threshold(symbol)` 方法：实现两步决策树。STEP 1 检查个股主力是否在出货（净流出 > 0.3 亿）→ 返回 -3pp。STEP 2 按板块资金分档 → 返回 -5pp 或 -8pp
- [x] 3.5 修改 `_check_sector_divergence`：在现有固定 `-3pp` 判断之前，先检查 `_is_extreme_panic()`，若是则用 `_get_panic_divergence_threshold` 替换硬编码阈值

## 5. P2: 合并"从未盈利"+"小盈转亏"为统一公式

- [x] 5.1 `_check_cost_stop` 有 HWM 分支合并为 `max(-3%, 振幅×0.40)` 统一公式，移除 max_profit_pct 边界判断
- [x] 5.2 `_calc_cost_stop_distance` 同步合并两档为统一公式

## 4. 验证

- [ ] 4.1 用豫能控股 07-20 行情数据验证 P0a：振幅 12.37%，从未盈利止损线应为 -4.95%
- [ ] 4.2 验证 P0b：60 分钟 MA10 > MA30 时止损被暂停，MA10 ≤ MA30 时正常执行
- [ ] 4.3 验证 P0c：极端恐慌 + 板块流入 + 个股未被出货 → 阈值从 -3pp 放宽到 -5pp/-8pp
- [ ] 4.4 验证 P0c 防线：极端恐慌 + 个股主力净流出 > 0.3 亿 → 阈值保持 -3pp
- [ ] 4.5 验证 `get_position_stop_distances` 对三个改动均返回正确距离值
