## Why

做T系统目前是纯"有底仓才做T"的实现：三重硬闸门（三层池红线、网关"无底仓禁低吸"、升级③"首开非底仓=human"）封死了 t 账户的任何新开仓路径，`paper_positions account_id='t'` 无任何建仓写入通道。原始设计蓝图（`final-t-plan.md` §③）规划的"打分制慢速建底仓、建仓走独立风控"从未落地。底仓是 T+0 回转的弹药，没有建仓能力，做T只能围着存量持仓空转——无法扩展标的、底仓不足时无弹药可回转，做T质量被锁死在天花板下。做T监控/网关/Agent 复核链路已稳定运行，补上"选股→建仓→衔接做T"正是当前缺口，且建仓策略需可参数化以便接入正在设计的历史回测模式做标定验证。

## What Changes

- **新增 t 底仓建仓通道**：独立的 `validate_build_position` + `build_gateway_execute` 校验执行链（白名单 + 共享熔断 + 金额/总底仓/regime/时机/封板校验），不改动现有 `validate_order` 对回转的保护；撮合复用 `executor.buy`（account_id='t'）。
- **新增建仓选股**：基于 `calc_t_quality` 四维 + 个股趋势闸门（20日线防单边下行）+ 补齐蓝图 w3/w4 减项（风险惩罚/成本占比）的打分与候选短名单；候选三级来源（用户指定 / stock 候选池 / Agent 自主扫描）。
- **新增建仓时机与规模**：冷静期后 + 盘中回踩窗口触发（价∧量比∧分时企稳，复用 t_monitor 判断）；单票当日只建 1 批、分批跨日；单笔/单标/总底仓三档硬上限（单笔≤净值 4/5/8%、单标累计≤10/15/20%、总底仓≤40/55/70%）。
- **新增建仓后衔接**：建仓当日盘后自动为 trade_date=D+1 生成 t_conditions（复用 `generate_conditions_for_live_pool` 模板），次日该标的进做T实盘池参与回转。
- **新增底仓再平衡**：被套（市值<成本 50%）禁高抛转只监控、总上限内受限补建、质量退化降级。
- **新增建仓工具面与审计**：`scan_t_candidates` / `build_t_position` / `auto_gen_conditions` / `rebalance_floors` / `get_floor_overview` 五个 Agent 工具；独立 `t_build_events` 审计表（不污染 t_triggers 做T事件流）。
- **新增账户资金初始化/调额入口**：`POST /t/account/capital-adjust` + `t_net_asset()` 统一读取 t 账户现值，替换 `t_gateway.py` 中硬编码的 `initial=200000`。
- **回测接线**：建仓策略全参数化（选股门/权重/时机/规模/分批/衔接），供动态建仓回测验证与 P4 敏感度扫描标定。

## Capabilities

### New Capabilities
- `t-position-building`: 做T底仓建仓能力——选股、建仓时机/规模、独立建仓通道、建仓后次日条件衔接、底仓再平衡、建仓审计与账户资金调额，与既有做T回转链路（t-account-trading）衔接但不改动其红线。

### Modified Capabilities
- （无主 spec 需求变更；本 change 引入新 capability，不改动既有 spec 的 REQUIREMENTS）

## Impact

- **Backend**：`backend/app/services/t_gateway.py`（新增 `validate_build_position`/`build_gateway_execute`/`t_net_asset()`，不改 `validate_order`）、`t_pool.py`（候选选股扩展）、`t_db.py`（`t_build_events` 表）、`t_eod.py`（再平衡衔接）、`backend/app/api/t_account.py`（建仓/调额端点）、`backend/app/database.py`（`t_build_events` 迁移，幂等）；新建 `t_build_*.py` 模块。
- **Agent 工具面**：`docker/dsh/bridge/lib/index.js` 新增 5 个建仓工具（区别于 stock 的 `/trades`）。
- **前端**：`TAccountPage.tsx` 扩展（候选池/建仓操作/底仓审计展示）。
- **回测模式**：动态建仓回测接线（参数化清单），与 t-backtest 共用沙盒账本。
- **账户体系**：t 账户资金显式初始化/调额（`paper_capital_adjustments`），不触碰 stock/golden_pit。
