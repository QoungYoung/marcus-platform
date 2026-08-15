## Context

做T回测（add-t-backtest-mode）已落地：单标的多日 m5 回放（`TBacktestEngine`）、数据预取（`t_backtest_data`：brze stk_mins + tushare 指数日线）、任务执行（`t_backtest_runner`：worker 轮询 + DB 落库 + LLM 复核客户端）、任务 API、bridge 沙盒复核端点。做T建仓（add-t-position-building）已落地：`t_build.py` 提供 `scan_t_candidates`/`build_score`/`trend_gate`/`build_sizing`/`validate_build_position`/`auto_gen_conditions_for_build`，但选股/打分读**实时日线**。前端已有 `TAccountPage`（/t-account）与旧黄金坑 `BacktestPage`（/backtest，已下架）。

## Goals / Non-Goals

**Goals:**
- 多标的多日组合回测：建仓规则模拟（Agent 选股建仓的历史化）→ 各标的多日做T → 组合收益与分项报告。
- 建仓规则复用且防前视：`t_build` 选股函数支持 `as_of` 截止的历史日线输入，生产行为不变。
- 前端 `TBacktestPage`：配置/发起/查看一步到位。

**Non-Goals:**
- 不做 LLM 真实选股建仓（规则模拟 Agent 决策；LLM 版留待进阶，review_mode=llm 仍可对做T触发复核）。
- 不做跨标的资金联动做T（组合内各标的独立做T，仅共享净值与建仓资金上限）。
- 不改动生产 t_build/t_backtest 的行为语义（重构只参数化）。

## Decisions

### D1. 组合引擎 = 建仓阶段 + 逐标的复用单标的引擎
`TBacktestEngine`（单标的）保持不动。新增 `TCombinedBacktestEngine`：
1. **建仓阶段**（窗口期初，T-1 及以前数据）：遍历候选 → `build_score_at(symbol, as_of)` + `trend_gate_at` 判定 → `build_sizing_at` 分配资金（累计 ≤ 净值×55%，单笔 ≤ 净值×5%）→ 建仓价 = 窗口首日开盘价 → 生成做T条件（用户条件或 `auto_gen_conditions_for_build` 逻辑）。
2. **做T阶段**：逐标的实例化 `TBacktestEngine`（同一数据目录、同一日期窗），条件用建仓生成的条件；组合账本汇总每日权益。
3. **汇总**：组合权益曲线 + 每标的明细 + 建仓决策审计。
- **理由**：单标的引擎已充分测试（14 用例），组合只做编排，避免重写回放核心。
- **替代**：扩展单标的引擎支持多 symbol——耦合触发/账本状态，否决。

### D2. t_build 选股函数参数化（as_of 注入，行为不变）
`build_score`/`trend_gate`/`build_sizing` 增加可选 `as_of: Optional[str]`（YYYY-MM-DD）与日线注入：
- 生产调用不传 → 行为与现状一致（实时日线）。
- 回测传 `as_of` → 内部 `_fetch_daily_bars(symbol, as_of=...)` 只取 ≤ as_of 的日线（`_fetch_daily_bars_tushare`/`_fetch_daily_bars_eastmoney` 已支持起止日期，加截止参数即可）。
- `scan_t_candidates` 同理加 `as_of`（组合建仓用）。
- **理由**：单一规则来源，防前视在数据层保证（对齐做T回放的 `_day_key` 约束）。
- **风险**：t_build 已有 6 个测试用例（test_t_position_building），参数化后全量回归。

### D3. 组合账本与资金分配
- 组合净值 `net_asset`（默认 20 万）；建仓资金上限 = 净值 × 55%（对齐 `build_sizing` 的 `MAX_TOTAL_FLOOR_RATIO` 语义）。
- 单标的建仓股数 = min(单笔上限净值×5% ÷ 首日开盘价，资金上限剩余 ÷ 开盘价) 向下取整到 100 股；不足 100 股拒绝并记录。
- 现金 = 净值 − Σ建仓市值；做T阶段每标的独立 `TBacktestLedger`（成本=建仓价），组合现金 = 初始现金 − Σ建仓支出 + Σ各标的做T现金变动。
- **理由**：与实盘 `build_sizing`/`validate_build_position` 的限额语义一致，报告可对照。

### D4. 数据预取多标的化
`t_backtest_data.prefetch_m5` 已按 symbol 独立——组合任务预取循环调用（逐标的 m5 + 指数 m5 一次 + 指数日线一次）。日线（指数 + 标的日线）供建仓规则 as_of 使用：新增标的日线预取（tushare `daily`，as_of 截止读取）。
- **注意**：指数 m5 已知 brze 无权限（每次失败重试 ~9s/交易日）——组合任务预取阶段加"指数 m5 跳过开关"（已知不可用则直接跳过，regime 走日线收盘口径），避免 3-4 分钟空转。

### D5. 任务模型与 API
- `t_backtest_tasks` 表加列（幂等迁移）：`symbols_json`（列表）、`build_mode`（bool）、`net_asset`（已有）、`build_limit_ratio`（默认 0.55）。
- `POST /t/backtest` 参数扩展：`symbols`（数组）、`build_mode`、`build_limit_ratio`；`symbols` 为空且 build_mode=on 时从候选池取（`scan_t_candidates(source='pool', as_of=起始日前)`）。
- `GET /t/backtest/{id}/report`：组合模式返回 `{portfolio: {...}, per_symbol: [...], build_decisions: [...], metrics, caliber_notes}`。
- 新增 `GET /t/backtest/candidates`：候选池查询（供页面"自动候选池"选择，含可T质量分）。

### D6. 前端 TBacktestPage
- 路由 `/t-backtest`（TopNav 加入口，图标与 TAccountPage 一致 RefreshCcw）。
- 结构：左侧任务列表（含组合任务标识/状态/摘要），右侧创建表单（候选标的多选输入框 + "从候选池加载"按钮、日期、条件模板下拉 [低吸/高抛/表达式/自动按建仓成本]、建仓开关、净值、review_mode），报告视图（指标卡 + Recharts 权益曲线 + 事件流表 + 成交/建仓明细表）。
- `client.ts` 加 `tBacktestApi`（create/start/cancel/list/report/events/candidates），复用 axios 模式。
- i18n zh/en 补文案；样式 `t-backtest-page.css` 对齐 `t-account-page.css` 视觉。
- **理由**：独立页面避免污染已下架的黄金坑 BacktestPage；复用 TAccountPage 视觉语言。

## Risks / Trade-offs

- **建仓规则历史化的数据依赖**：`build_score` 依赖 Tushare 日线（个股 daily）——回测需预取标的日线；数据缺失时该标的建仓判定降级（打分保守/跳过），报告标注。
- **组合回测时长**：N 标的 × 单标的回放 + 预取（每标的 ~1 分钟 m5 + 日线）——页面提示预估；先小组合（3-5 标的）验证。
- **建仓模拟 ≠ 真实 Agent 决策**：规则模拟是确定性的（同参数同结果），LLM 版不在本次范围——报告口径声明中明示"建仓决策为规则模拟"。
- **t_build 参数化回归**：as_of 注入改动共享函数 → 生产路径全量回归（test_t_position_building 等），保持默认参数行为不变。
- **指数 m5 预取空转**：组合预取显式跳过已知不可用的指数 m5（env/常量开关），避免每任务 3-4 分钟无谓重试。

## Migration Plan

1. `t_build` 参数化（as_of/日线注入）+ 全量回归 → 单独提交。
2. 数据层：标的日线预取 + 指数 m5 跳过开关。
3. 组合引擎（建仓阶段 + 编排 + 汇总）+ 任务模型扩展（迁移加列）。
4. API 扩展（symbols/build_mode/报告结构/candidates）。
5. 前端 TBacktestPage + 构建 + 部署（服务器 compose up --build frontend/backend/worker；dsh 无改动）。
6. 回滚：组合为新增能力，`build_mode=off` + 单 symbol 即旧行为；前端新页面独立路由。

## Open Questions

- 候选池"自动选股"的标的数量上限（默认取前 N 个打分达标标的，N 可配——页面加数量参数即可，实现期定）。
- 组合报告 CSV 导出是否需要（当前页面内展示 + JSON API 已覆盖，导出留待体验增强）。
