## Context

现状（见 proposal.md Why + `t-position-design/reports/t1-current-state.md` 全量调研）：做T系统是纯"有底仓才做T"，`t_gateway.validate_order` 的 buy 分支（`t_gateway.py:217-220`）无条件拒绝无底仓买入，`paper_positions account_id='t'` 无任何建仓写入路径，做T Agent 无建仓工具。原始蓝图（`final-t-plan.md` §③）规划的"打分制慢速建底仓、建仓走独立风控"未落地。可复用资产：`t_pool.calc_t_quality`（可T四维打分）、`generate_conditions_for_live_pool`（条件模板）、`get_sellable_ledger`（T+1 账本语义，零改动即自洽）、`gateway_execute` 撮合段、`candidate_pool_monitor` 时间护栏、`t_monitor` 企稳判断、`t_eod` 生命周期。

本设计基于团队终审定稿（`t-position-design/reports/t2-strategy.md` / `t3-risk.md` / `t4-review.md`，M1-M5 修正已并入）。

## Goals / Non-Goals

**Goals:**
- 打通"选股→建仓→次日衔接做T→再平衡"闭环，建仓作为 T+0 弹药的独立增量通道
- 严格隔离：不改动 `validate_order`/`gateway_execute` 对回转的保护；建仓只落 account_id='t'
- 建仓与回转共享全局熔断（t_risk_state/t_daily_state），建仓金额计入日回转额
- 全参数化，可被 t-backtest 动态建仓回测 + P4 敏感度扫描标定

**Non-Goals:**
- 不改变做T回转链路（触发/复核/回转网关）的任何既有行为
- 不做多账户资金自动划转（仅提供人工调额入口）
- 不实现 stock/golden_pit 账户的任何改动
- 不在本 change 落地 P4 参数最终固化（交付分档初值 + 扫描脚本）

## Decisions

### D1 独立建仓校验链（核心架构）
新建 `validate_build_position(symbol, price, volume, ...)` + `build_gateway_execute(...)`，独立于 `validate_order`/`gateway_execute`；撮合复用 `executor.buy`（`PaperTradingEngine(account_id='t')`）底层落库设施。
- **为什么**：建仓（无底仓首买）与回转（有底仓中继）校验语义根本不同；混入 `validate_order` 会引入回归风险（t1 硬约束）。`kind=entry` 仅是入口标记，不作为经主链放行的依据（防"跳过无底仓闸门"的宽松 clone，t4 M3）。
- **备选**：在 `gateway_execute` 加 entry 分支——否决（回归风险）；复用 stock `/trades`——否决（账户错位）。

### D2 T+1 账本零改动
建仓走 `paper_trades(direction='买入')` → `get_sellable_ledger` 的 `sellable = volume − today_buy` 自动使建仓当日 sellable=0。账本不区分建仓买入与做T买回，统一 T+1 锁定，**不改账本代码**（t4 采纳 t3 §4）。
- 边界：建仓当日该标的不可卖出（sellable=0 天然挡死）；有旧底仓标的追加建仓后，"先卖后买"回转只用旧可卖额度。

### D3 规模口径裁定（M2/M2b）
- 总底仓上限统一为 **t 净值 × 40/55/70%**（保守/标准/激进），弃 t2 的 80%（吃掉回转现金退化为死仓）。
- 单笔 ≤ 净值 4/5/8%、单标累计 ≤ 10/15/20%（硬闸门）。
- 文档明确：单票占比相对 **t 净值**（非总底仓）；实际并行票数 = `min(MAX_FLOOR_SYMBOLS, 总量上限/单票占比)`（标准档约 3 只触 55% 上限）；`MAX_FLOOR_SYMBOLS=10` 是宽松上限。
- 基准统一走新建 `t_net_asset()`（读可用资金+持仓市值现值），替换 `t_gateway.py:146,264` 的 `initial=200000` 硬编码（t3 §3.3）。

### D4 单票当日单批、分批跨日（M1）
`BUILD_MAX_PER_SYMBOL_PER_DAY=1`：同一标的当日只建 1 批，单标总底仓分批（如 3 批）分布在 3 个建仓日。理由：当日多批在同一建仓日追高叠加，且与"回踩+企稳确认"择时冲突。

### D5 次日衔接时机（M5）
建仓当日 **盘后（Worker 低频任务，如 15:05）** 生成 `trade_date=D+1` 的 t_conditions（复用 `generate_conditions_for_live_pool` 模板，用当日收盘成本锚价）。弃"次日盘中生成"（浪费早盘 + 锚价不干净）。建仓当日只写审计、不生成当日条件（sellable=0 无法触发）。

### D6 建仓审计独立表
新建 `t_build_events` 表（event_type='build_position'、决策来源 agent|human、委托/成交价量、理由、regime、网关校验结果、建仓前后持仓），复用 `human_confirm 超时→cancelled` 处置哲学但表独立——**不污染 t_triggers 做T事件流**（t3 §6）。

### D7 熔断联动与回转额口径
- 建仓先跑 `check_breakers()`：STOP_ALL/人工锁/日亏熔断(-2%)/连续亏损(≥3) 阻断自动建仓；日亏预警(-1%) 降 human。
- 建仓名义金额计入 `daily_turnover_amount`（共享 3×净值上限）；数据层按来源拆分"回转名义额/建仓名义额"两个子账本（R1），审计清晰。
- regime 门：ACTIVE 自动 / CAUTIOUS 仅 human（MANUAL_ONLY）/ HALT 全禁（含人工）。

### D8 人工升级清单（B 版）
首开新标的=human、单笔超阈值=human、CAUTIOUS 自动=human、连续亏损期=human+禁自动、近跌停(≤-8%)=human、日亏预警破=human、HALT=禁。默认自动、异常升级，不由 LLM 自判（对齐 `classify_escalation` 哲学）。

### D9 复用清单（不重造）
| 能力 | 复用 |
|---|---|
| 可T打分/条件模板 | `t_pool.calc_t_quality` / `generate_conditions_for_live_pool` |
| 熔断/封板/账本 | `t_gateway.check_breakers` / `_limit_status` / `_near_limit_down` / `get_sellable_ledger` |
| 时间护栏 | `candidate_pool_monitor`（冷静期/PI_WINDOWS/max_daily_auto_buys） |
| 企稳确认 | `t_monitor` 企稳表达式（not_new_low/lower_shadow/vol_shrink_rebound） |
| 撮合落库 | `executor.buy` / `paper_engine._apply_trade`（account_id='t'） |
| 迁移范式 | `_apply_t_account_migration`（幂等，扩展 `t_build_events`） |

### D10 参数归口
新建 `t_build_params`（或 `t_risk_state.params_json`）：单笔%/单标%/总量%/日上限/冷却期/时机窗口/选股阈值，全部分档初值，标注"P4 ±30% 敏感度扫描后固化"（复用 `scripts/t_sensitivity_scan.py` 模式）。

## Risks / Trade-offs

- [独立通道被实现成"跳过无底仓闸门"的宽松 clone] → D1 强制完整校验链接线（白名单/熔断/金额/总量/regime/时机/封板/候选池），t4 M3 三封死点入验收标准
- [M2b 口径误配（实现者按"10 票×15%"满配）] → D3 显式写清 `min(MAX_FLOOR_SYMBOLS, 总量/单票)` 公式 + 回测联合网格（R5）
- [建仓金额计入日回转额语义混淆] → D7 子账本拆分 + 审计来源标记（R1）
- [建仓当日浮亏不计日亏的时滞被误读] → 文档写明"建仓次日起该标的回转盈亏才进日亏统计"（R2）
- [硬编码 initial=200000 口径漂移] → D3 `t_net_asset()` 统一读现值 + 调额端点（R3/R4）
- [LLM 建仓工具直触下单] → D8 + 网关唯一放行；工具只提交请求，不绕过校验

## Migration Plan

- **落地顺序（最小可行闭环 ①②③④）**：
  1. ① 独立建仓通道 + `t_build_events` 审计 + `t_net_asset()`/调额端点（含迁移，幂等）
  2. ② `scan_t_candidates` 选股 + 候选短名单
  3. ③ `build_t_position` 建仓（规模/分批/时机/人工升级）
  4. ④ `auto_gen_conditions` 次日衔接（Worker 盘后任务）
  5. ⑤ `rebalance_floors` 再平衡（复检 t_eod 生命线）
  6. ⑥ 动态建仓回测接线 + P4 参数标定
- **兼容性**：`validate_order`/`gateway_execute`/`t_triggers`/`t_daily_state`/`t_risk_state` 结构不动；新增表与端点；迁移幂等可重跑。
- **回滚**：建仓功能开关（如 `t_build_events.enabled` 或参数置零）可随时禁建仓，回转链路不受影响；新增表可保留（无害）。

## Open Questions

- 建仓选股 Agent 自主扫描（L3）的日频上限（初值 ≤50 票/日、1s 节流）与分钟线数据源覆盖，以 P0 探针/回测实测为准
- `t_build_params` 独立表 vs `t_risk_state.params_json` 的存储选择，P4 前可定
- 调额端点（`POST /t/account/capital-adjust`）是否配套前端 UI，或先 API 后 UI
