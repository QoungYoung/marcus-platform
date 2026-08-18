## Context

做T系统已有完整的"底仓建仓"能力（t-position-building：build_t_position / build_gateway_execute，account_id='t'，t_build_events 审计，t_build_scan_results 候选，t_conditions 次日衔接），其建仓时机为"回踩低吸"（冷静期后 + 距高点回撤>=1% + 量比<2 + 分时企稳）。前期为捕捉"下跌V反+放量突破"类标的（如 002384 东山精密 8 月反弹），已在主账户侧加了趋势突破监控（trend_breakout_monitor）并落地"长期候选池不可移除"；但主账户侧会与回踩池/长期池混用、且可能触碰 stock 资金。本 change 把该功能迁入 t 账户：独立选股通道 + 复用 t 建仓网关 + 短线出场条件，账户资金严格隔离。历史回测支撑（本地 parquet，2023-2026，1062 例）：<100亿小票、触发日买入、+8%止盈/-5%止损，胜率约 60%、盈亏比约 2.2、平均持有约 3.5 天。

## Goals / Non-Goals

**Goals:**
- 为 t 账户提供独立于主账户回踩池的趋势突破短线通道（选股->建仓->+8%/-5%/5日出场）
- 全链路只动 account_id='t'，不触碰 stock/golden_pit 资金与候选池
- 复用 t 建仓网关与做T执行器，保留全部硬风控，仅放开"回踩低吸时机"这一项
- 参数化（市值/突破阈值/仓位档/止盈止损/持有上限/节流），可调可回测

**Non-Goals:**
- 不改变主账户回踩池/长期池既有逻辑（保留"长期候选池不可移除"）
- 不实现跨账户资金划转；t 资金不足即跳过
- 不做 T+0 日内回转增强；本能力是 2-5 日短线（建仓当日 T+1 不可卖）
- 不在本 change 做 P4 参数最终固化（交付初值 + 可标定入口）

## Decisions

### D1 账户隔离：所有读写限定 account_id='t'
- 扫描结果写 t_build_scan_results（source='trend_break'）；建仓走 build_gateway_execute（内部固定 account_id='t'）；平仓走 gateway_execute（account_id='t'）。
- 不调用 stock 的 /trades、不读写 candidate_pool / long_term_pool 用于下单。
- 停用/移除主账户侧 trend_breakout_monitor 的自动入池（防其影响其他账户），保留其代码用于回测或改造成只读报告。
- 若 t 可用资金不足 -> 跳过本轮，绝不自其他账户补。

### D2 日频入池 + 实时触发两段式
- 入池（日频，盘后）：当日主力净流入>0 且 5 日累计>0；市值<100亿（total_mv 换算亿元）；收盘放量突破近 20 日高点（量>=1.5x 近20日均量）；MA20 转上。
- 触发（实时，盘中）：东财 push2 f62 实时主力净流入>0 + 量比>=阈值；实时源失败（如 EM 代理 502/非交易时段）-> 降级为"次日开盘竞价/低吸复核"或跳过，不盲买。
- 数据源优先级：日频用 Tushare moneyflow/本地 parquet；实时用东财 push2（复用 em_sector_flow._http_get），带超时与降级。

### D3 建仓模式 trend_break（在 t 建仓网关内扩展）
- build_t_position 新增 build_mode='trend_break'：跳过 confirm_build_timing 的回踩/量比/企稳确认；其余校验（白名单、check_breakers、regime、时段、封板、规模、日建仓上限、单票单批）全部保留。
- 首开自动放行沿用 ai_led 语义（allow_first_open=True）；决策来源记为 ai_led/trend_break。
- 撮合复用 build_gateway_execute 的 executor.buy（account_id='t'）。

### D4 独立规模档
- t_build_params 新增 trend_break_* 参数：single_order_pct=0.30、per_symbol_cap=0.30、total_cap=0.60、max_symbols=3、mcap_max_yi=100、break_high_n=20、vol_mult=1.5、tp5/tp8/sl5/hold_days=5、scan_daily_max=50、scan_interval_s=1、realtime_confirm=True。
- 仅 trend_break 模式读取该档；既有建仓/回转仍用 4/5/8% 等旧档。
- 基准净值统一 t_net_asset()（25 万为示例值，实际取 t 账户现值）。

### D5 出场条件
- 建仓次日（D+1）为该标的生成 t_conditions（或 trend_break 专用条件记录）：
  - 高抛减半：浮盈 >= +5% 卖 50%（sell_target = 成本*1.05，量=半仓）
  - 高抛清仓：浮盈 >= +8% 卖剩余（sell_target = 成本*1.08）
  - 止损：浮亏 <= -5% 全清（stop_loss_price = 成本*0.95）
  - 超时：D+1 起第 5 个交易日收盘仍未了结 -> 市价清仓
- 出场执行经 gateway_execute（account_id='t'），sell 走可卖额度（T+1 自动）；止损/超时卖腿不被日亏熔断阻断（对齐止损豁免语义）。

### D6 超时平仓实现
- 优先用 t_conditions 表达式能力（若有"持仓 N 交易日"字段/表达式）；否则由新增 t_trend_break 监控线程在盘中按（建仓日 + N 交易日）计算并触发平仓，同一逻辑不污染既有 t_triggers 回转事件流（独立来源标记）。

### D7 扫描与监控线程
- 新增 backend/app/services/t_trend_break.py：TrendBreakMonitor（日频扫描入池 + 盘中实时复核 + 超时平仓），注册进 worker（与 t_build_service 并列，60s 低频、account_id='t'）。
- 日频扫描节流（<=50 票/日、>=1s 间隔）；实时复核失败降级；所有异常 catch 不连坐既有监控。

### D8 配置与开关
- t_build_params 或环境变量 T_TREND_BREAK_ENABLED 控制总开关；默认关闭（灰度过），开启后才扫描/建仓/平仓，避免影响现有 t 账户。

## Risks / Trade-offs

- [trend_break 模式被实现成"跳过所有风控"的宽松 clone] -> D3 强制仅豁免"回踩时机确认"一项，其余校验逐一保留并列入验收矩阵
- [账户混用（误写 stock/长期池）] -> D1 硬隔离：执行器固定 account_id='t'、扫描只写 t_build_scan_results；测试断言 stock/golden_pit 表与资金零变化
- [实时源失败导致盲买] -> D2 降级/跳过策略 + 开关，实时不可用绝不自动下单
- [仓位 30% 过重放大回撤] -> D4 独立档 + 总仓 60% 上限 + 组合 -3% 停机（可配），且默认开关关闭灰度
- [T+1 当日卖出被误触发] -> D5/D6 持有天数自 D+1 起算，D0 卖出被账本 sellable=0 天然拦截

## Migration Plan

- 落地顺序（最小闭环）：
  1. D1 账户隔离骨架 + 停用主账户 trend_breakout_monitor 自动入池（保留代码）
  2. D2 日频扫描入池（t_build_scan_results, source='trend_break'）
  3. D3 build_t_position trend_break 模式（建仓网关扩展）
  4. D4 规模档 + D5 出场条件（t_conditions 生成）
  5. D6 超时平仓监控 + D7 worker 注册（T_TREND_BREAK_ENABLED 默认关）
  6. 回测接线（用本地 parquet 回放 trend_break 入池/出场，输出与固定口径对比）
- 兼容性：既有 t 建仓/回转链路不动；新增参数档与开关；迁移幂等（如需新列）。
- 回滚：T_TREND_BREAK_ENABLED=0 即整体停用；主账户侧改动可单独回退。

## Open Questions

- 超时平仓用 t_conditions 表达式 vs 专用监控线程（D6），实现时按 t 系统表达式字段能力确定
- trend_break 建仓是否需在 TAccountPage 前端展示候选/持仓/条件（先 API，后按需 UI）
- 日频数据源：优先 Tushare moneyflow / 本地 parquet，若生产缺数据则用东财日频降级
- P4 标定：+8%/-5% 是否针对 t 账户净值波动进一步 ±30% 扫描（复用 t_sensitivity_scan 模式）
