## Context

做T系统方案已经过 4 位专家三轮辩论收敛并产出实施蓝图（`.agent-teams/t-account-planning/reports/final-t-plan.md`），P0 四合一探针已实测验证全部数据前提（`backend/scripts/p0_probe/p0_report.md`）。动机与范围见 proposal.md。本设计基于以下既有底座：

- **多账户模拟盘**：`paper_accounts` 注册表 + `paper_account_info/paper_positions/paper_orders/paper_trades` 按 account_id 维度隔离，`_apply_paper_account_migration`（database.py:183）幂等迁移范式。
- **Worker 监控器**：`worker_main.py` 注册 4 个 daemon 监控器（stop_loss 31s / position_tier 33s / candidate_pool 37s / long_term 300s），`candidate_pool_monitor.py` 有 `time.sleep(20)` 初始偏移先例；PG 作控制通道（worker_status/worker_commands）。
- **实时行情**：`core/xueqiu_engine.py::get_stock_quote`（腾讯 qt.gtimg.cn，默认缓存 TTL=300s，支持 `use_cache=False` 直连）。
- **分钟线（P0 实测）**：腾讯 ifzq mkline（m1 500根/m5 320根/m60 320根，100% 成功率 ~200ms）+ 新浪（300 根，价差 <0.1%）+ brze tushare 代理（stk_mins/rt_k/rt_min/rt_min_daily 权威校验，与腾讯 1min 价差 0.0000%）；Tushare gyzcloud 月卡已到期（403）。
- **regime 现成数据**：`market_diagnosis` 表（state + score_trend/score_oscillation/score_extreme）+ `_get_market_regime_for_calc`（indicator.py:1146）。
- **DSH 桥接**：`docker/dsh/bridge/lib/index.js` 的 `/chat` 路由（POST 唤醒 Agent）已在生产验证；写工具（place_order 等）已注册。
- **量比 bug**：`indicator.py:2224` 现为 `turnover_rate/2.0`（固定除历史日均，无时段归一）。

## Goals / Non-Goals

**Goals:**
- 落地四层合一做T系统：market_regime 环境闸门 × 复合确认触发 × 事件驱动执行 × 当日可卖额度账本。
- 三权分立执行链：Worker 事件发生器 / Agent 复核决策者 / 网关唯一放行者，网关 account_id 白名单隔离、不影响 stock 主账户。
- 数据源按 P0 实测落地：实时触发腾讯 qt 30s；分钟级三源（腾讯 ifzq 主 + 新浪备 + brze 权威校验）。
- 分阶段交付：P1 选股（账户+三层池+打分）→ P2 监控触发（表+状态机+量比）→ P3 桥接唤醒（网关+账本）→ P4 闭环审计（参数标定）。

**Non-Goals:**
- 不改现有 stock/golden_pit 主策略的既有权重与风控行为（网关只包 t 账户）。
- 不做真实盘口/L2/真实滑点（模拟盘天花板，滑点参数化假设）。
- 不做全市场扫描选股（只从三层池中已持仓标的做T）。
- 本 change 不包含回测验证做T策略（做T参数标定在 P4 用历史回放/敏感度扫描）。

## Decisions

### D1. 数据源架构：腾讯 qt 实时 + 腾讯 ifzq 分钟线（主）/ 新浪（备）/ brze（权威校验）
- **选择**：实时触发走腾讯 qt 30s 轮询（P0 实测 100% 成功率、单轮 avg 186ms）；分钟级数据（可T质量/量比基准/企稳确认/指数分钟线）走腾讯 ifzq mkline 为主源，新浪为降级冗余源，brze tushare 代理为权威校验源（三源价差 <0.1%）。
- **备选**：Tushare stk_mins 官方/gyzcloud —— 已被 P0 实测否决（月卡 2026-08-14 到期返回 403）。
- **理由**：免费源无 SLA，三源冗余 + 退避重试可覆盖；brze 提供官方字段（含 amount）交叉验证；`use_cache=False` 直连绕过 xueqiu 300s 缓存。

### D2. TMonitor 监控层：现有监控器同款 daemon 线程 + 分层采样 + 初始偏移
- **选择**：`start_t_monitor(executor)` 注册进 worker_main，30s 周期，初始偏移 `time.sleep(20)` 错峰；核心底仓（≤10-20 只）腾讯 qt `use_cache=False` 直连 + `ThreadPoolExecutor(≤5)` 并发；观察池 30s-1min 缓存取价；`_is_trading_time` 门控。
- **备选**：3-5s 高频轮询 —— 被 xueqiu 缓存 TTL=300s 与反爬风险否决；秒级时效由"命中即计算 + 本地条件单 + 主动唤醒"承载。
- **理由**：复用已验证的监控器模式，避免打爆行情源。

### D3. 触发桥接：Worker 主动唤醒，Agent 不轮询
- **选择**：TMonitor 命中后写 t_triggers(pending) 并主动 POST bridge `/chat` 唤醒 Agent（附触发上下文快照）；`UPDATE...WHERE status='pending' RETURNING` 原子消费防重复；桥不可达降级 30s 低频轮询兜底。
- **备选**：Agent 高频轮询 t_triggers —— 白烧 LLM；dsh-schedule 5min —— 粒度太粗。
- **理由**：事件驱动延迟最低、成本可控，符合既有"PG 作控制通道"范式。

### D4. t_triggers 状态机：pending → (auto_ready | human_confirm) → executed | blocked | cancelled
- **选择**：Agent 复核后按网关判定的 mode 分流：常态(auto)→auto_ready→网关二段实时断言→executed/blocked；异常(human_confirm)→挂起人工（超时 2min 自动 cancelled）。
- **理由**：默认自动、异常升级，做T窗口不被 LLM+网关双时延锁死；安全由确定性网关承担而非 LLM 自判。

### D5. 网关实现：place_order 包装层 + 三阶校验 + account_id 白名单
- **选择**：在 `MarcusVNPyExecutor` 的 buy/sell 之前新增统一 `place_order` 包装层：①硬闸门（裸空/跌停/STOP_ALL/白名单，O(1) 快路径）→②中间账本（可卖底仓断言、买腿≤可卖底仓、日亏/回转额熔断）→③建议层（单笔%、冷却、价差/成本比、频次护栏，仅告警限频）；落单前二段实时断言（最新持仓/价格/跌停/熔断）。
- **备选**：网关只在 Agent 侧（HTTP 风控校验）—— 无法覆盖 Worker 条件单等所有下单来源；需在统一入口强制。
- **理由**：所有来源（Agent/条件单/未来策略）必经网关；account_id 白名单保证 stock 主账户不受影响（risk-auditor 架构盲点修正）。

### D6. 可卖额度账本：卖腿扣、买腿回补、半边腿锁
- **选择**：t 账户专用账本（t_ledger 或 paper_positions 扩展），卖腿下单原子扣减可卖额度（UPDATE...WHERE 可卖≥下单量 RETURNING），买腿成交回补；卖出在途锁定该标的禁止买腿；可卖底仓 L0-L3 分档（0.5×/1.0×/1.0-1.5×+日回转额上限）。
- **理由**：T+1 回转额度精确记账是"做T变裸加仓/隔夜"的生命线防线（quant-trader 不可让步点）。

### D7. regime 三层合成单一闸门：t_regime_state 表 + TMonitor 前置 GATE
- **选择**：L1 日频基准读 `market_diagnosis`（现成）+ Tushare/腾讯指数日线；L2 日内动态前哨用腾讯 qt 指数实时跌幅/破5日线（分钟级）；L3 硬保险丝（沪深300跌>2%→无条件 HALT）；合成输出三态 ACTIVE/CAUTIOUS/HALT + 量能解读符号；`t_regime_state` 表每交易日一行，TMonitor 写 t_triggers 前先过 GATE（BLOCKED 不写 / MANUAL_ONLY 挂人）。
- **备选**：Agent 事后判断 regime —— 被否决（环境判断必须做进监控层硬闸门，不交 LLM 自觉）。
- **理由**：复用现成 market_diagnosis 不重造；单一闸门杜绝多开关矛盾指令。

### D8. 数据表设计（五张新表）
- `t_conditions`（条件注册表：条件元组 + regime_gate + armed 状态机 + benchmark_turnover_profile JSONB）
- `t_triggers`（事件流：snapshot JSONB 分层 + 状态机 + claimed_by 原子消费）
- `t_regime_state`（环境闸门状态：regime/gate_low_buy/gate_high_sell/interpret_sign/intraday_lowbias）
- `t_daily_state`（日级账本：累计回转额/净回转头寸/熔断 flag/触发计数）
- `t_risk_state`（全局风控：STOP_ALL/regime 档/连续亏损计数）
- 全部按 `_apply_t_account_migration()` 幂等创建，account_id 维度隔离，对齐 `_apply_paper_account_migration` 范式。

### D9. 分钟线数据客户端
- **选择**：新增 `backend/scripts/p0_probe/data_sources.py` 已验证的取数逻辑沉淀为服务层模块（腾讯 ifzq mkline / 新浪 minline / brze tushare 代理客户端），brze 按卖家约束**单线程串行 + 间隔≥1s + 失败 sleep 1-3s 重试**、实时分钟 freq 大写（1MIN/5MIN/60MIN）；供选股打分、量比基准、企稳确认、regime L2 复用。
- **理由**：P0 已实测三源可用且价差 <0.1%，无需再引入未验证依赖。

### D10. 量比归一公式落地
- **选择**：修正 `indicator.py:2224` `turnover_rate/2.0` → `[当前累计换手 × (240/已开盘连续分钟)] / 近N日同刻均值`；`benchmark_turnover_profile` 用腾讯 m5 6日历史/新浪 300 根/brze stk_mins 构造（P0 已验证，无需 P2 自积累）。
- **理由**：消除早盘/高开系统性误报，P0 量能 U 型曲线证实"同刻基准"必要性。

## Risks / Trade-offs

- [腾讯/新浪免费接口无 SLA，盘中可能限流] → 三源冗余 + 退避重试 + 双源交叉验证（P0 实测 20 只并发无风控迹象，盘中真实负载 P2 再验）。
- [brze 代理单线程串行 1s/只，20 只≈20s/轮] → 只用于低频选股/指标/基准计算，实时触发走腾讯 qt；代理 token 配置化。
- [Tushare gyzcloud 到期影响日线选股/regime L1] → 迁移腾讯 fqkline day（P0 已验证可用）或续费。
- [regime 滞后（震荡转下跌初跌窗口）] → L2 日内动态 + L3 硬保险丝 + 初跌领先预警缓解；仍是最高滑点/接刀风险源，实盘重点观察。
- [分钟线历史深度受限（腾讯 m1 仅 500 根≈2.5 日）] → 量比基准取 m5 6日足够；不足时退回落盘自积累（P2 intraday_volume_profile）。
- [Agent 决策延迟极端行情滑点] → 默认自动、异常升级缩到最小；网关确定性规则兜底。
- [做T高频回转手续费侵蚀] → 最低价差/成本比前置过滤（>价差空间 15-20% 不触发）+ 滑点参数化假设。

## Migration Plan

1. **数据库**：`_apply_t_account_migration()` 幂等迁移，注册 t 账户 + 五张新表；可重复执行，不破坏现有 paper 表。
2. **数据源**：新增分钟线客户端模块与配置（腾讯 ifzq/新浪/brze token），独立于现有 xueqiu_engine；腾讯 qt 保持现状，做T侧显式 `use_cache=False`。
3. **监控器**：`start_t_monitor` 注册进 worker_main，错峰启动；通过 worker_status/worker_commands 控制开关。
4. **网关**：place_order 包装层默认只拦截 t 账户（白名单路由），stock 主账户路径先灰度（默认放行）再验证无回归。
5. **回滚**：各阶段独立可回滚——t 账户/表可停用（enabled=0）、TMonitor 可 stop、网关可临时 bypass t 账户、STOP_ALL 一键全停。

## Open Questions

- 做T触发后的成交价模拟：是否需要更真实的撮合滑点模型（当前为参数化假设），还是 P3 用模拟撮合价直接验证即可。—— 可在 P3 验证后决定，不改变 spec/架构。
- 前端做T页面的交互细节（条件列表/事件流/审计展示形态）—— P1-P3 后端落地后按实际数据设计，不改变 spec 行为契约。
