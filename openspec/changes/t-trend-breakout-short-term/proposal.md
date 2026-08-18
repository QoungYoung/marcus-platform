## Why

系统当前把"独立于回踩池的趋势突破检测"落在主股票账户（stock）候选/长期池方向（前期已加 trend_breakout_monitor 与"长期候选池不可移除"），但它会与主账户回踩池/长期池共用标的与门控，存在账户资金混用风险，且与"只做小市值短线、+8%/-5%/5日"的短线策略语义不匹配。基于本地 parquet 历史回测（2023-2026，577 只/1062 例）：这类"下跌V反>=25% + MA20 仍向下 + 末端超买"状态，<100亿小票在"触发日买入 + 止盈+8%/止损-5%"下胜率约 60%、盈亏比约 2.2、平均持有约 3.5 天、20 日内再创新高比例 97%；大票则接近零期望甚至负期望。用户要求：把该功能放入做T账户（account_id='t'），不触碰其他账户资金。

## What Changes

- **做T账户（t）新增"趋势突破短线"能力**：独立于主账户回踩池（candidate_pool）与长期池（long_term_pool），选股/建仓/平仓全链路只作用于 account_id='t'。
- **日频入池 + 实时触发**：收盘后用日频资金流（当日主力净流入>0 且 5 日累计>0）+ 市值<100亿 + 放量突破近 20 日高点 + MA20 转上，命中写入 t 专用候选（t_build_scan_results，source='trend_break'）；次日盘中用实时主力净流入与量比复核后才下单，实时不可用时降级复核，不盲买。
- **建仓复用 t 建仓网关**：经 build_t_position / build_gateway_execute（account_id='t'），新增 trend_break 模式：跳过"回踩低吸"时机确认，保留熔断/时段/封板/单笔/单标/总底仓/日建仓上限等硬风控；首开自动放行沿用 ai_led 语义。
- **短线出场**：建仓次日生成 t 条件：+5% 减半、+8% 清仓、-5% 硬止损、5 个交易日后超时平仓；执行经 gateway_execute（account_id='t'），受 t 账户熔断/STOP_ALL/可卖额度（T+1）约束。
- **规模参数（以 25 万净值为例）**：trend_break 独立仓位档，单笔 <= 净值 30%（约 7.5 万/票）、单票 <= 30%、总仓 <= 60%、并行 <= 3 只；参数入 t_build_params 可调。
- **严格账户隔离**：扫描、建仓、平仓、审计全部限定 t 账户；t 资金不足时跳过而非从其他账户划转；停用主账户侧此前为同一目的加的 stock trend_breakout_monitor（防其触碰其他账户）。

## Capabilities

### New Capabilities
- t-trend-breakout-short-term: 做T账户·趋势突破短线交易能力——日频选股入池、实时触发复核、经 t 建仓网关建仓、+8%/-5%/5日 短线出场，账户隔离于 stock/golden_pit。

### Modified Capabilities
- t-position-building（既有）：build_t_position 增加 trend_break 模式（跳过回踩时机确认），t_build_params 增加 trend_break 规模档；不改变其既有建仓行为与红线。
- t-account-trading（既有）：t_conditions 增加"持有 N 交易日超时平仓"表达能力（或由 trend_break 专用监控实现），不改变既有回转触发语义。

## Impact

- **Backend**：backend/app/services/t_build.py（trend_break 建仓模式 + 规模档）、新增 t_trend_break.py（日频扫描/实时复核/超时平仓监控，account_id='t'）、t_db.py（如需新字段）、t_account.py（扫描/状态端点）；移除/停用主账户侧 backend/app/services/trend_breakout_monitor.py 的自动入池。
- **执行链路**：建仓/平仓全部经 build_gateway_execute / gateway_execute（account_id='t'），不触碰 stock 的 /trades。
- **数据**：复用 t_build_scan_results / t_build_events / t_conditions；不新增其他账户表；t 账户资金独立（paper_accounts account_id='t'）。
- **配置**：t_build_params 新增 trend_break_*（市值上限、涨幅/突破阈值、仓位档、止盈止损、持有上限、日扫描节流、实时复核开关）。
