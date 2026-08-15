## 1. t_build 参数化（as_of 历史日线注入，行为不变）

- [x] 1.1 `build_score`/`trend_gate`/`build_sizing` 增加 `as_of` 与日线注入参数：生产不传行为不变；回测传 as_of 时 `_fetch_daily_bars*` 只取 ≤ as_of 日线
- [x] 1.2 `scan_t_candidates` 增加 `as_of`（组合建仓选股用）
- [x] 1.3 全量回归（test_t_position_building 等既有用例全绿，确认生产行为不变）

## 2. 数据层扩展

- [x] 2.1 `t_backtest_data` 新增标的日线预取（tushare daily，供建仓规则 as_of 使用）
- [x] 2.2 指数 m5 预取跳过开关（brze index_min 已知无权限，避免每任务 3-4 分钟失败重试空转）
- [x] 2.3 组合任务预取编排（逐标的 m5 + 日线 + 指数一次）

## 3. 组合回测引擎

- [x] 3.1 `TCombinedBacktestEngine`：建仓阶段（遍历候选 → build_score_at/trend_gate_at 判定 → build_sizing_at 资金分配 ≤ 净值×55%、单笔 ≤ 5% → 建仓价=首日开盘 → 记录否决原因）
- [x] 3.2 建仓后做T条件生成（用户条件或 auto_gen_conditions_for_build 逻辑：低吸×0.98/复归+0.4%/高抛+1.5%/止损-3%）
- [x] 3.3 做T阶段：逐标的实例化 `TBacktestEngine`（同数据目录/日期窗），组合现金与权益每日汇总
- [x] 3.4 组合报告结构：组合级指标 + 每标的分项（建仓价/成本/做T盈亏/触发统计/否决原因）+ 建仓决策审计 + 口径声明（新增"建仓为规则模拟"）

## 4. 任务模型与 API

- [x] 4.1 表迁移（幂等）：t_backtest_tasks 加 symbols_json / build_mode / build_limit_ratio
- [x] 4.2 `POST /t/backtest` 支持 symbols（数组）/build_mode/build_limit_ratio；symbols 空且 build_mode=on 时取候选池（scan_t_candidates as_of=起始日前）
- [x] 4.3 `GET /t/backtest/{id}/report` 返回组合结构（portfolio + per_symbol + build_decisions）
- [x] 4.4 新增 `GET /t/backtest/candidates`（候选池查询，含可T质量分，供页面选择）

## 5. 前端 TBacktestPage

- [x] 5.1 路由 /t-backtest + TopNav 入口 + i18n（zh/en）
- [x] 5.2 创建表单：候选标的多选/手填/从候选池加载、日期、条件模板、建仓开关、净值、review_mode
- [x] 5.3 任务列表（状态/进度/组合标识/摘要）+ 报告视图（指标卡 + Recharts 权益曲线 + 事件流 + 成交/建仓明细）
- [x] 5.4 `client.ts` 加 tBacktestApi（create/list/report/events/candidates/cancel）
- [x] 5.5 前端构建（tsc + vite build）通过，dist 更新

## 6. 测试与部署

- [ ] 6.1 `backend/tests/test_t_combined_backtest.py`：组合编排/建仓模拟防前视/资金上限分配/组合权益汇总/报告结构/候选池缺省
- [ ] 6.2 全量回归（新增用例 + 既有 t 用例）
- [ ] 6.3 提交推送 + 服务器部署（compose up --build backend worker frontend；dsh 无改动）+ 页面验证
