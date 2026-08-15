# Tasks — add-t-position-building

> 参考 specs/t-position-building/spec.md（行为契约）与 design.md（技术设计，D1-D10）。
> 落地顺序遵循 design.md Migration Plan：最小可行闭环 ①→②→③→④，再 ⑤ 运营强化、⑥ 标定。

## 1. 独立建仓通道与账户基准（设计 D1/D3/D6/D7）

- [x] 1.1 数据库迁移：新增 `t_build_events` 表（幂等，扩展 `_apply_t_account_migration` 或新增 `_apply_t_build_migration`），字段含 event_type/account_id/symbol/委托价/数量/金额/成交价/decision_source/reason/regime/gateway_result/position_before/position_after/status/created_at
- [x] 1.2 新增 `t_net_asset()` 工具：从 `paper_account_info(account_id='t')` 读可用资金+持仓市值得当前 t 净值；`t_gateway.py` 中 `_daily_pnl_pct`（:146）与日回转额上限（:264）的 `initial=200000` 硬编码改为调用该工具
- [x] 1.3 新增调额端点 `POST /api/v1/t/account/capital-adjust`（走 `paper_capital_adjustments` + 更新 paper_account_info，幂等校验）
- [x] 1.4 新增 `validate_build_position(symbol, price, volume, reason, ...)`：校验链 = 账户白名单 + `check_breakers()`（STOP_ALL/人工锁/日亏熔断/连续亏损）+ 单笔/单标累计/总底仓上限（三档，读 t 净值）+ regime 门（ACTIVE/CAUTIOUS-human/HALT 禁）+ 冷静期 9:30-9:45 + 午后 13:00 后禁 + 日建仓上限（自动≤3/人工≤5/单票≤1）+ 涨跌停封板（复用 `_limit_status`/`_near_limit_down`）+ 候选池白名单；返回 {pass, reason, level, warn}
- [x] 1.5 新增 `build_gateway_execute(...)`：调 `validate_build_position` → 过则 `executor.buy`（`PaperTradingEngine(account_id='t')` + `MarcusVNPyExecutor(account_id='t')`）→ 成功更新日账本（建仓名义额入 `daily_turnover_amount`，来源标记 build）→ 写 `t_build_events`
- [x] 1.6 确认 `validate_order`/`gateway_execute` 一字未改（回归保护），`kind=entry` 仅作入口标记不作放行依据
- [x] 1.7 测试：建仓风控矩阵（无底仓放行/STOP_ALL 拒/总底仓超限拒/冷静期拒/单票当日二次拒/HALT 拒/近跌停升 human）+ T+1 账本（建仓当日 sellable=0，次日 release）+ 调额后基准更新

## 2. 建仓选股与候选短名单（设计 D9，spec: 建仓选股）

- [x] 2.1 扩展候选选股：基于 `calc_t_quality` 四维 + 个股趋势闸门（20 日线方向/均线排列，单边下行排除）+ 风险惩罚/成本占比减项（补齐蓝图 w3/w4），输出建仓打分 `build_score`（初值 CAND_SCORE_MIN=0.55 / BUILD_SCORE_MIN=0.60）
- [x] 2.2 候选来源三级：用户指定（API 参数）/ 既有 `candidate_pool.get_candidate_pool()`（stock 候选池）/ Agent 扫描接口；每票带 source 标记（user/pool/scan）落候选短名单
- [x] 2.3 新增 `scan_t_candidates` 服务/端点：日频低频扫描（初值 ≤50 票/日、1s 节流），数据源不可用时降级日频近似（兜底）
- [x] 2.4 测试：打分硬门槛（价差≤0 剔除）、趋势闸门（单边下行排除）、候选来源标记、扫描节流

## 3. 建仓时机、规模与人工升级（设计 D3/D4/D8，spec: 建仓时机与规模/触发确认/人工升级）

- [x] 3.1 建仓触发确认：回踩支撑区（距当日高点回撤≥1%）∧ 量比 < 2.0（复用修正后量比归一）∧ 分时企稳（复用 `t_monitor` 企稳判断 not_new_low/lower_shadow/vol_shrink_rebound）
- [x] 3.2 建仓规模计算：单笔 ≤ 净值 4/5/8%、单标累计 ≤ 10/15/20%、总底仓 ≤ 40/55/70%（三档按 regime 缩放）；文档/常量注明 `实际并行票数 = min(MAX_FLOOR_SYMBOLS, 总量上限/单票占比)` 口径（M2b）
- [x] 3.3 单票当日单批：`BUILD_MAX_PER_SYMBOL_PER_DAY=1`，分批跨日（M1 裁定）
- [x] 3.4 建仓人工升级清单（B 版）：首开新标的=human、单笔超标准档阈值=human、CAUTIOUS 自动=human、连续亏损期=human+禁自动、近跌停(≤-8%)=human、日亏预警(-1%)=human、HALT=禁（含人工）；human_confirm 复用超时→cancelled 处置
- [x] 3.5 新增 `build_t_position` 端点：入参 symbol/price/volume/reason，走 3.1-3.4 全链后调 `build_gateway_execute`
- [x] 3.6 测试：时机拒建（追高/放量）、单票当日二次拒、各档上限、B 清单升级分流、HALT 全禁

## 4. 建仓后次日条件衔接（设计 D5，spec: 建仓后次日衔接）

- [x] 4.1 新增 `auto_gen_conditions` 服务：为当日建仓成交标的生成 `trade_date=D+1` 的 t_conditions（复用 `generate_conditions_for_live_pool` 模板：target=成本×0.98 / reinform=×1.004 / sell_target=×1.015 / stop_loss=×0.97 / vol_ratio=1.5 / not_new_low）
- [x] 4.2 Worker 盘后低频任务（15:05）：scan t 账户当日建仓 + live 池缺失条件的标的，统一补生成次日条件；建仓当日不生成当日条件
- [x] 4.3 测试：建仓当日无当日条件且 sellable=0；次日条件存在且可触发；重复建仓不重复生成

## 5. 底仓再平衡（spec: 底仓再平衡）

- [x] 5.1 新增 `rebalance_floors` 服务：底仓市值 < 成本×50%（复用 `t_eod.check_floor_lower`）→ 转只监控禁高抛 + 总/单标上限内受限补建；质量退化 → 降级出实盘池；达标且现金允许 → 上限内补建
- [x] 5.2 再平衡触发：日频评估 + regime 切换时触发；补建走建仓网关全链（首开新标的仍需人工）
- [x] 5.3 测试：跌破保留下限禁高抛、补建受限、降级流转

## 6. Agent 工具面与前端（spec: 建仓工具面）

- [ ] 6.1 bridge 新增 5 个工具：`scan_t_candidates` / `build_t_position` / `auto_gen_conditions` / `rebalance_floors` / `get_floor_overview`（含参数 schema 与渲染），均调 t 专用后端端点，不直触下单
- [ ] 6.2 做T Agent 系统提示词补充建仓工作流指引（选股→建仓→衔接→再平衡闭环），与既有做T回转工具共存
- [ ] 6.3 前端 `TAccountPage.tsx` 扩展：候选短名单、建仓操作（含人工确认）、建仓审计列表、底仓总览
- [ ] 6.4 测试：工具端到端（scan→build→auto_gen→overview），人工确认流程，审计可查

## 7. 回测接线与参数标定（spec: 建仓策略参数化）

- [x] 7.1 建仓策略参数集中配置（`t_build_params` 或 `t_risk_state.params_json`）：选股门槛/权重/时机窗口/规模比例/分批规则/衔接参数，全部分档初值并标注 P4 扫描
- [ ] 7.2 动态建仓回测接线：t-backtest 支持"动态建仓口径"（与固定底仓 C1 口径对比），按配置参数模拟选股→建仓→次日衔接（**依赖 t-backtest 立项实现，本 change 未落地**）
- [x] 7.3 敏感度扫描：复用 `scripts/t_sensitivity_scan.py` 模式，对建仓参数 ±30% 网格扫描（含"单票占比×总量上限×MAX_FLOOR_SYMBOLS"联合网格），输出标定建议
- [ ] 7.4 测试：参数化回测可跑、结果与固定底仓口径可对比、扫描脚本输出收敛区间（扫描脚本已验证输出可行区间；回测对拍依赖 7.2）

## 8. 文档与验收

- [x] 8.1 更新 `final-t-plan.md` §⑪ 实现状态（建仓段落地记录）或新增建仓设计文档引用
- [x] 8.2 全量回归：`python -m pytest backend/tests -q`（含 `test_t_account_trading.py`），确认做T回转既有测试全绿（222 passed；8 个既有失败为 PySide6 环境缺失，与改动无关）
- [ ] 8.3 前端 `npm run build` 通过，dist 更新；bridge 插件重启后工具注册验证（前端构建已通过、dist 已更新；bridge 工具注册与做T会话提示词注入需 DSH 容器重启后在服务器验证）
