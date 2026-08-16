## Why

做T回测在 8 月板块轮动市（2026-08-06~08-14）表现不佳：守护关 #71 -0.30%、守护开 #74 -0.37%。同期申万一级行业数据显示这是「权重阴跌 + 结构轮动」行情：行业窗口涨跌中位数 **+4.96%**，等权买入所有行业 +3.44%，动量追强 TOP5 +5.41%——钱在结构里、在强势行业里。而做T自动选股选出的 5 只票全部落在阴跌行业（火力发电/化学制药/互联网/广告包装/陶瓷），与电子(+17.2%)/通信(+20.2%)/机械设备(+14.1%)等强势行业完全脱节。根因：`scan` 只评估「可T质量」（振幅/流动性/日内波动的可操作性），不评估方向；震荡市模式甚至偏好「跌不动也涨不动」的票，在轮动市里恰恰最没有赚钱效应。选股与板块方向脱节，做T再勤也只是在下跌行业的日内波动里捡钢镚。

## What Changes

- **行业强度因子（评分合并）**：新增申万一级行业近 5 日动量（`sw_daily` 日线计算），并入做T质量评分：`final_score = quality × 0.7 + industry_strength × 0.3`（权重可配）。行业强度标准化为 [0,1]，行业涨幅 ≥ 0 记为正向。
- **行业强势过滤（默认开，可关）**：自动选股（`scan`）排除「所属申万一级行业近 5 日涨幅 ≤ 0」的候选；`relax_mode`（震荡市）下同样生效，保证过滤与生产选股语义一致。
- **回测轮动换仓（高切低）**：滚动建仓/滚动扫描模式下，持仓每日比对候选池：持仓票所在行业跌破中立线（近 5 日涨幅 ≤ 0）或票自身质量跌破阈值 → 记 `sector_switch` 事件并卖出；同时若候选池出现「行业强度更高且质量达标」的票 → 换入。换仓阈值、冷却期参数化默认关闭（`rotation_enabled=false`），打开后先跑对照回测验证。
- **行业数据预取**：`sw_daily` 申万一级行业（L1，31 个）按日拉取并缓存（复用现有 tushare 代理，回放零网络）。
- **事件与前端**：事件流新增 `sector_excluded`（行业过滤剔除）与 `sector_switch`（轮动换仓）；前端任务表单加行业过滤/轮动开关与行业强度展示。

## Capabilities

### New Capabilities

- `t-sector-rotation`: 做T选股的板块轮动增强——行业强度评分因子、行业强势过滤、滚动模式轮动换仓（高切低）；回测先行，参数默认关闭轮动。

### Modified Capabilities

（无——现有 openspec/specs 的板块选择能力（`dual-track-sector-selection`、`bull-regime-selection`、`sector-5d-aggregation`）属于主交易/GoldenPit 子系统，本次仅增强做T选股子系统行为，不改动其需求。）

## Impact

- **代码**：
  - `backend/app/services/t_backtest_data.py`：新增 `prefetch_industry_daily` / `load_industry_daily`（sw_daily 全行业，按 trade_date 缓存）
  - `backend/app/services/t_build.py`：`BUILD_PARAMS_DEFAULT` 新增 `industry_strength_weight`(0.3)、`sector_filter_min_pct`(0.0)、`rotation_enabled`(false) 等；`build_score` 合并行业强度因子；`scan_t_candidates` 应用行业过滤
  - `backend/app/services/t_pool.py`：`_quality_from_ohlcv` 保持无方向语义，行业强度在 `t_build` 层合并（生产 `calc_t_quality` 同步口径）
  - `backend/app/services/t_backtest.py`：滚动模式接入轮动换仓（`sector_switch` 事件、卖出/买入撮合复用现有路径）
  - `backend/app/services/t_backtest_runner.py`：`auto_select_symbols_rolling` 拉取行业数据并过滤；任务创建参数透传
  - `backend/app/api/t_backtest.py`：create 请求新增参数
  - `frontend/src/pages/TBacktestPage.tsx` + `frontend/src/api/client.ts`：开关/权重/事件标签
- **数据**：新增 tushare `sw_daily`（申万一级行业）日线拉取，约 31 行业/交易日/任务；缓存体积可控
- **验证**：08-06~08-14 同窗口对照（全关 vs 仅过滤 vs 过滤+轮动）；05-18~05-29 历史窗口回归防过拟合
- **风险**：行业动量因子可能引入「追涨杀跌」效应（高切低在趋势市亏损）→ 必须用两个窗口对照；轮动换仓增加调仓成本与回测复杂度 → 默认关闭、参数化验证
- **不做**：不接 `dual-track-sector-selection` 的 AI 主线识别（属于主交易子系统）；不改实盘持仓换仓（实盘联动留待单独 change）；行业粒度固定申万一级 L1（不做概念级细分）
