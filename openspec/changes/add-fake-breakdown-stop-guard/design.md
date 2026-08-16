## Context

现状（见 proposal.md - Why）：做T止损在 `t_backtest.py::_process_stop_loss`（回测）与 `stop_loss_monitor`（实盘）中均按"bar 最低价 ≤ 止损价"触发，无任何假跌破识别。#71 回测（震荡市模式）中 13 次止损 9 次卖飞（利欧 +16.7%、金山 +14.1%），均为单根下影线插针后收盘收回。

数据可用性（已实测）：
- brze 代理 `stk_mins freq=1min` ✅（241 根/日）；`stk_mins 5min` ✅
- tushare `daily_basic.turnover_rate` ✅（日换手率）
- tushare `cyq_perf` ✅（筹码分布：成本 5/15/50/85/95 分位、获利比例）
- 指数分钟 ❌（brze index_min "tenant key expired"）；逐笔/L2/分时大单 ❌（代理未开放）
- 约束：回测引擎零网络回放（数据预取阶段一次性拉取落盘缓存）；实盘止损监控 30s 轮询、可用实时行情

## Goals / Non-Goals

**Goals:**
- 建立纯规则"假跌破判定"（收盘确认 + 收回幅度 + 分钟企稳 + 缩量 + 支撑位），回测与实盘共用同一实现与参数
- 回测数据预取补充 1min、日换手率、筹码分布（守卫输入）
- 用 #71 同窗口重跑验证：卖飞次数下降、组合收益不恶化（或改善）

**Non-Goals:**
- 不接入 L2/逐笔/分时大单（代理未开放，属实盘增强，另行立项）
- 不引入机器学习/LLM 判定（可解释、可回测、低风险优先）
- 不改动建仓/选股逻辑（仅止损侧）

## Decisions

### D1: 判定函数独立成模块 `t_stop_loss_guard.py`（回测/实盘共用）
纯函数 `evaluate_stop(bars_up_to, stop_price, params) -> {action: "stop"|"hold", reason, reset_stop: Optional[float]}`。
- 输入全部来自预取/实时数据（OHLC/vol/1min/日线/筹码），不依赖引擎内部状态
- 回测 `_process_stop_loss` 与实盘 `stop_loss_monitor` 都只调用它 → 满足 spec "共用同一守卫"
- 备选：在 t_gateway 内联判定 → 否决（网关保持纯撮合校验，职责分离）

### D2: 默认开启"收盘确认 + 收回幅度 1%"，分钟企稳/缩量/支撑位为可调增强
- 最小改动消掉 #71 两笔大卖飞：收盘确认（两例收盘均在止损上方）+ 收回幅度 1%
- 分钟企稳（1min 连续 N 根收回）需要 1min 预取，作为增强默认开（N=5）
- 支撑位感知用 `cyq_perf.cost_50pct`（筹码成本中位）与近 20 日最低价做"前低"；默认阈值 1.5%
- 所有开关进 `BUILD_PARAMS_DEFAULT`（t_build_params 可覆盖），回测/实盘读同一 `_params()`

### D3: 止损基准重置语义
假跌破跳过止损后，止损基准重置为该交易日收盘价（防止同一价位反复被插针）。若重置后再次收盘 ≤ 新基准则正常止损。
- 备选：保持原止损价 → 否决（同一价位反复触发、循环洗出）

### D4: 回测数据预取扩展（`t_backtest_data.py`）
- 标的 `prefetch_m5` 旁新增 `prefetch_m1`（brze stk_mins freq=1min，按交易日逐日），缓存 `m1/{symbol}.json`
- `prefetch_stock_daily` 扩展字段：换手率（tushare `daily_basic`，与日线同表按 trade_date 合并）、筹码分布（`cyq_perf`）
- 仅对"进入做T阶段的标的"预取 1min（建仓后），控制体积；换手/筹码随日线一次性拉取
- 回放零网络约束保持：所有新数据在预取阶段落盘，`load_* ` 读取

### D5: 实盘接入点
`stop_loss_monitor` 执行卖出前调用同一守卫；实盘数据源为实时行情（腾讯/东财），1min 实时可得，换手率实时有（腾讯 turnover_rate），筹码分布为日频（用最近一日 cyq_perf）。
- 实盘守卫默认参数与回测一致；差异（数据时点）在日志标注

## Risks / Trade-offs

- [收盘确认延迟止损] 单边下跌日：盘中破位不卖、收盘才卖，执行价可能更差 → 参数 `stop_close_confirm` 可关；用 #71 及历史窗口回测验证净效果
- [1min 预取体积/耗时] 每标的天数 × 241 根 JSON → 只对建仓标的预取；缓存体积可接受（任务级目录）
- [支撑位误判] cyq_perf/前低仅日频，插针日可能失真 → 阈值保守（1.5%）且需回测敏感性
- [回测-实盘粒度差异] 回测用预取 1min，实盘用实时 1min → caliber_notes 声明
- [参数过度拟合] 阈值默认值基于 #71 单窗口 → 参数敏感性测试 + 多窗口验证后再固化

## Migration Plan

1. 新增 `t_stop_loss_guard.py` + 参数（默认：close_confirm=true, recovery_pct=1.0, confirm_bars=5, volume_filter=true, support_proximity_pct=1.5）
2. 回测：`t_backtest_data` 预取扩展 → `_process_stop_loss` 接入守卫
3. 用 #71 同窗口（08-05~08-14 震荡市模式）重跑，对比卖飞次数/收益
4. 实盘：`stop_loss_monitor` 接入守卫（默认同参数）
5. 回滚：参数全关即恢复原盘中触发行为；代码回退仅需还原 `_process_stop_loss`/监控接入点

## Open Questions

- 收盘确认在"收盘价 ≤ 止损价"用收盘价成交还是仍按 min(开盘, 止损价)？→ 暂定维持现有撮合价口径（min(开盘,止损)），回测对比后可调
- 1min 企稳确认的 N 根窗口是否按"当日剩余 bar"限制（避免跨日）→ 暂定仅当日有效，跨日由次日重新评估
