## Why

做T回测目前只能"给定标的和条件"回放做T回转，且没有便捷页面（只有 REST API 与 Agent 工具 `run_t_backtest`）。用户要回答的核心问题是"**如果 7 月就让 Agent 自己选股建仓、然后做T，收益如何**"——需要把**选股建仓（t_build 规则）纳入回测**，做成**多标的多日组合回测**，并用**前端页面**让配置/发起/看报告一步到位。

## What Changes

- **多标的多日组合回测**：回测任务支持候选标的列表（页面手填 + 系统候选池两源），回测引擎对每个标的跑**建仓规则模拟**（复用 `scan_t_candidates`/`build_score`/`trend_gate`/`build_sizing` 的判定逻辑，数据源注入历史），达标者分配仓位建底仓，再逐标的多日做T回转，汇总**组合收益**（组合权益曲线 + 每标的明细）。
- **建仓规则历史化（防前视）**：`t_build` 的选股/打分函数（`build_score`/`trend_gate` 等）当前读实时日线——回测中改为**按回测日期截止（as_of）**的日线输入（T-1 及以前），与做T回放同一防前视约束。
- **任务参数扩展**：`symbols`（列表，空则取做T候选池 candidate 层）、`build_mode`（on=组合建仓模拟 / off=固定底仓单标的，兼容现有）、组合净值与每标的仓位上限（对齐 `build_sizing` 规则）、底仓股数。
- **报告扩展**：组合级指标（总收益/胜率/最大回撤/买入持有对比）+ 每标的分项（是否建仓/建仓价/做T盈亏/触发统计）+ 建仓决策明细（打分/趋势/被否原因）。
- **前端 TBacktestPage**：新路由 `/t-backtest` + TopNav 入口。配置表单（候选标的多选/手填/自动候选池、日期范围、条件模板、建仓开关、净值、review_mode）→ 任务列表（状态/进度/摘要）→ 报告（指标卡 + Recharts 权益曲线 + 事件流 + 成交/建仓明细）。复用 `TAccountPage` 视觉风格与 `backtestApi` 模式。
- **API 扩展**：`POST /t/backtest` 支持组合参数；`GET /t/backtest/{id}/report` 返回组合+分项；新增候选池查询（供页面候选选择）。

## Capabilities

### New Capabilities

- `t-combined-backtest`: 多标的多日组合做T回测——Agent 选股建仓模拟（t_build 规则历史化）+ 组合做T回放 + 组合/分项报告 + 前端配置与查看页面。

### Modified Capabilities

<!-- 无：现有 t-backtest（单标的做T回测）行为不变，组合模式为新增能力；t_build 选股函数仅参数化数据源（as_of 注入），生产行为不变。 -->

## Impact

- **Backend 扩展**：`t_backtest_runner.py`（任务参数 symbols/build_mode/组合净值 + 多标的执行编排）、`t_backtest.py`（组合引擎：建仓模拟 + 多标的调度 + 组合汇总；复用单标的 `TBacktestEngine`）、`api/t_backtest.py`（组合参数 + 候选池查询 + 组合报告）。
- **Backend 参数化重构（行为不变）**：`t_build.py` 的 `build_score`/`trend_gate`/`build_sizing` 增加 `as_of`/日线注入参数（回测用历史日线，生产默认实时）。
- **数据层**：`t_backtest_data.py` 预取扩展为多标的（逐标的 m5 + 指数 + 日线）；日线缓存供建仓规则（as_of 截止）使用。
- **前端新增**：`frontend/src/pages/TBacktestPage.tsx` + `styles/t-backtest-page.css` + 路由/i18n/TopNav + `client.ts` 的 tBacktestApi。
- **测试**：`backend/tests/test_t_combined_backtest.py`（组合编排/建仓模拟防前视/资金分配/报告结构）+ 前端构建验证。
- **依赖**：无新增第三方依赖（Recharts/axios 已在用）。
