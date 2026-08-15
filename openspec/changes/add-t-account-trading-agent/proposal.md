## Why

用户需要一个**专用做T（T+0 回转）账户 + 做T Agent**：Agent 从已持仓底仓中寻找适合做T的标的、设置监控条件（如"放量下跌到XX元"）、条件触发后激活 Agent 决策执行，以日内高抛低吸摊低成本、增厚收益。当前平台只有日频级别的止损/加仓/候选池监控，无分钟级数据支撑、无独立回转账户、无"环境闸门 + 复合确认 + 可卖额度账本"的做T能力——方案经 4 位专家（量化/风控/数据/市场）三轮辩论收敛，P0 数据探针已验证可行性（腾讯 qt 100% 成功率、分钟线三源可用、可T质量指标可分）。

## What Changes

- 新增**做T专用账户 `t_account`**：注册进 `paper_accounts`，独立资金与独立风控参数集（保守/标准/激进分档，P4 经 ±30% 敏感度扫描标定后固化）。
- 新增**三层池 + 可T质量打分制选股**：底仓候选池（打分制建仓，不做T）→ 做T实盘池（已持仓 + 可T质量达标 + 过 regime 门 + 底仓≥下限，唯一允许触发）→ 观察池（缓冲）；**禁止无底仓建仓式做T**。
- 新增 **TMonitor 监控层**（Worker 30s 轮询）：分层采样（核心底仓腾讯 qt 直连 ≤10-20 只 / 观察池 30s-1min）、盘中量比时段归一（修正现有 `turnover_rate/2.0` bug）、滞回/去抖/armed 状态机、复合企稳确认；命中写 `t_triggers` 并**主动 POST /chat 唤醒 Agent**（不轮询、不直接下单）。
- 新增 **market_regime 单一环境闸门**（三层合成）：L1 日频基准（复用现成 `market_diagnosis`）+ L2 日内动态前哨（腾讯指数实时跌幅/破5日线）+ L3 硬保险丝（沪深300跌>2%→无条件 HALT）；三态 ACTIVE/CAUTIOUS/HALT，做进监控层写事件之前；同指标按 regime 反向解读（量能符号）。
- 新增 **执行链三权分立 + 风控网关**：Worker=事件发生器 / Agent=复核决策者（默认自动、异常升级 6 类清单）/ 网关=唯一放行者（`place_order` 包装层，account_id 白名单隔离，三阶校验：硬闸门→账本→建议层，落单前二段实时断言）。
- 新增 **T+1 当日可卖额度原子账本**：卖腿下单扣可卖额度、买腿成交回补，先卖后买、半边腿未落定不启动另一半；可卖底仓 L0-L3 分档取代"≥2倍"静态因子。
- 新增 **t_conditions / t_triggers / t_regime_state / t_daily_state / t_risk_state 五张表** + `t_triggers` 状态机（`pending → (auto_ready|human_confirm) → executed|blocked|cancelled`，`UPDATE...WHERE status='pending' RETURNING` 原子消费）。
- 新增 **做T风控机制**：STOP_ALL 总开关、日亏损熔断、孤儿单超时处置、滑点参数化假设 + 最低价差/成本比前置过滤、高抛接回 + 踏空熔断、底仓保留下限、尾盘 14:45 后禁新开仓、跌停禁买、全量审计日志。
- **数据源落地**（P0 实测修正）：实时触发走腾讯 qt 30s（100% 成功率，单轮 avg 186ms）；分钟级数据（可T质量/量比基准/企稳确认）走**腾讯 ifzq m1/m5（主）+ 新浪（备）+ brze tushare 代理（权威校验）三源**；Tushare gyzcloud 月卡已到期，日线选股/regime L1 建议迁移腾讯 fqkline day。

## Capabilities

### New Capabilities

- `t-account-trading`: 做T专用账户（t_account 注册/独立资金/独立风控参数分档）+ 三层池流转 + 可T质量打分制选股（价差空间/O-C回归/往返度三代理）。
- `t-monitor-trigger`: 做T监控与触发——TMonitor 分层采样、盘中量比归一、滞回/去抖/armed 状态机、复合企稳确认、t_conditions/t_triggers 表与状态机、Worker 主动唤醒 Agent。
- `t-regime-gate`: 做T环境闸门——market_regime 三层合成（L1 日频 market_diagnosis + L2 日内动态前哨 + L3 硬保险丝）、三态语义、初跌领先预警、量能反向解读、t_regime_state 表、TMonitor 前置 GATE。
- `t-execution-risk`: 做T执行与风控——三权分立执行链、place_order 网关包装层（account_id 白名单 + 三阶校验 + 二段实时断言）、当日可卖额度原子账本、可卖底仓 L0-L3 分档、异常升级 6 类清单、熔断与 STOP_ALL、孤儿单处置、滑点与价差过滤、尾盘归平、审计日志。

### Modified Capabilities

<!-- 无——做T为全新能力域，现有 trading/agent/market spec 的既有行为不变；place_order 网关包装层为新增包装，不改现有 /api/v1/trades 契约。 -->

## Impact

- **后端**：`backend/app/database.py`（新增 `_apply_t_account_migration` 幂等迁移，注册 t 账户 + 五张新表）；`backend/app/worker_main.py`（注册 `start_t_monitor`）；新增 `backend/app/services/t_monitor.py`、`t_regime_service.py`、`t_gateway.py`、`t_ledger.py` 等模块；`backend/app/api/indicator.py`（修正量比公式 bug、复用 `_get_market_regime_for_calc`）；`core/xueqiu_engine.py`（腾讯 qt 直连 `use_cache=False`）；新增分钟线数据源客户端（腾讯 ifzq / 新浪 / brze）。
- **桥接**：DSH bridge（`docker/dsh/bridge/lib/index.js`）新增做T条件管理/查询/唤醒相关能力；Worker 命中后 `POST /chat` 主动唤醒。
- **前端**：新增做T账户/监控条件/触发事件页面（账户状态、条件列表、事件流、审计）。
- **数据/依赖**：Tushare gyzcloud 月卡到期（stk_mins 403）→ 分钟线迁腾讯 ifzq + 新浪 + brze 三源；日线选股/regime L1 建议迁移腾讯 fqkline day；brze 代理需按卖家约束单线程串行调用。
- **P0 探针已就绪**：`backend/scripts/p0_probe/`（data_sources.py + probe1-8 + p0_report.md）已验证全部数据前提。
