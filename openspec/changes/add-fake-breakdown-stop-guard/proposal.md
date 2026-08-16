## Why

做T回测（#71，震荡市模式）暴露止损被"假跌破/洗盘"反复洗出：13 次止损中有 9 次卖飞（≥2%），其中利欧股份止损 @5.20 后最高 6.07（+16.7%）、金山股份止损 @16.55 后最高 18.89（+14.1%）。共同特征：止损按"盘中最低价 ≤ 止损价"即触发，当日却收出长下影线并收回止损上方——单根下影线插针就把底仓洗掉，随后价格快速反弹。当前止损只认价格、不认结构，缺少任何假跌破识别能力。

## What Changes

- **回测数据预取补充**（假跌破识别的输入）：
  - 标的 **1 分钟分钟线**（brze `stk_mins freq=1min`，已实测可得）
  - **日换手率**（tushare `daily_basic.turnover_rate`，已实测可得）
  - **筹码分布**（tushare `cyq_perf`：成本分布/获利比例，已实测可得）
- **止损假跌破识别（做T，回测与实盘共用同一纯规则实现）**：
  - **收盘确认止损**：盘中最低价跌破只记预警，必须收盘价 ≤ 止损价才执行
  - **收回幅度过滤**：收盘相对止损价收回 ≥ 阈值（默认 1%）→ 判定假跌破，跳过本次止损并重置止损基准
  - **分钟级企稳确认**（可选，默认开）：跌破后连续 N 根 1min 收回止损上方才确认
  - **缩量破位过滤**：跌破 bar 成交量相对近 N 日均量缩量 → 洗盘概率高，提高确认要求
  - **支撑位感知**：止损价贴近前期低点 / 筹码成本峰时，破位需要更强确认（防在支撑位下方被插针洗出）
- **参数化**（t_build_params 可调）：`stop_close_confirm` / `stop_recovery_pct` / `stop_confirm_bars` / `stop_volume_filter`
- **回测验证**：用 #71 同窗口（2026-08-05~08-14，震荡市模式）重跑，对比止损卖飞率与组合收益

## Capabilities

### New Capabilities
- `t-stop-loss-guard`: 做T止损的假跌破/洗盘识别——收盘确认、收回幅度、分钟企稳、缩量过滤、支撑位感知；回测与实盘共用同一实现与参数。

### Modified Capabilities
（无——现有 openspec/specs 未覆盖做T止损行为，止损规则目前只在代码中）

## Impact

- **代码**：
  - `backend/app/services/t_backtest_data.py`：预取新增 1min、日换手率、筹码分布
  - `backend/app/services/t_stop_loss_guard.py`（新）：假跌破判定纯函数（回测/实盘共用）
  - `backend/app/services/t_backtest.py`：`_process_stop_loss` 接入守卫（回测侧）
  - `backend/app/services/t_gateway.py` / 止损监控：实盘止损执行前接入同一守卫
  - `backend/app/services/t_build.py`：`BUILD_PARAMS_DEFAULT` 新增止损守卫参数
- **数据**：新增 tushare `daily_basic` / `cyq_perf` 调用与缓存；1min 预取量增加（每标的天数 × ~241 根）
- **风险**：收盘确认可能把止损延迟到收盘（当日单边下跌时执行价更差）→ 必须回测验证参数；1min 预取增加耗时与缓存体积
- **不做**：L2/逐笔/分时大单（代理未开放、需付费源）；历史新闻情绪（AKShare 新闻无历史深度）
