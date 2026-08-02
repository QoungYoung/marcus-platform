## Why

当前黄金坑 DCA 统一在每日 10:05 执行买入，所有指数无差别对待。分钟级数据分析（2020-2026年，10只ETF，覆盖1289-4264个交易日）表明：不同指数在不同市场状态下存在显著的日内最优买入时点。创业板指在黄金坑日 09:36 买入比收盘价节省 0.125%，是普通日的 2 倍；科创50 在普通日尾盘最便宜（14:44 省 0.044%），但坑日开盘后最优（09:37 省 0.086%，放大 4 倍）。每天 0.05-0.12% 的价差看似微小，但年化 50+ 次定投的复利效应不容忽视，且实现成本几乎为零。

## What Changes

- 在 `CHINA_INDICES` 的每个指数配置中新增 `buy_time` 和 `buy_time_pit` 两个字段，分别指定普通日和黄金坑日的推荐买入时间
- DCA 执行服务在每日定投时读取对应时点配置，按要求的时间窗口执行买入
- 默认值：A 股 ETF 普通日 `09:36`，坑日 `09:36`（多数指数的早盘折扣在坑日更明显）；科创50 普通日 `14:44`、坑日 `09:37`；中证1000 普通日 `09:36`、坑日 `14:44`

## Capabilities

### New Capabilities
- `golden-pit-intraday-timing`: 基于分钟级历史数据的 per-index 最优日内买入时点配置，区分普通日和黄金坑日

### Modified Capabilities
- `golden-pit-dca-schedule`: DCA 买入执行从固定 10:05 改为按 per-index 配置的 `buy_time` / `buy_time_pit` 时间执行

## Impact

- **DCA 服务**: `backend/app/services/golden_pit_dca_service.py` — 执行时间逻辑变更
- **指数配置**: `backend/app/services/golden_pit_service.py` — `CHINA_INDICES` 新增 `buy_time` / `buy_time_pit` 字段
- **定时任务**: `config/tasks.yaml` — DCA 任务可能需要拆分或调整触发时间
- **无 API 变更、无数据库迁移、无前端变更**
