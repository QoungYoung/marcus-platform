## Why

黄金坑策略目前直接买入宽基 ETF，但入坑时市场恐慌，成分股中会有被错杀的龙头。如果在黄金坑入坑日，用现有的 `check_entry_filters` 入场过滤体系筛选指数成分股，只买入通过技术面+资金面检测的个股，收益率能否跑赢纯买 ETF？这个回测将给出量化答案。

## What Changes

- 新增回测脚本 `scripts/backtest_golden_pit_stocks.py`，在黄金坑入坑日的 14:55 时刻，对指数成分股逐一运行入场过滤，模拟买入通过检测的股票，计算持有期收益并与 ETF 基准对比
- 通过 tushare `index_weight` 接口获取各宽基指数的历史月度成分股列表，缓存到本地 parquet
- 将 `check_entry_filters` 的三层过滤逻辑抽取为可离线调用的纯函数，接受预计算的指标值，供回测脚本复用
- 回测结果按指数、按时间窗口汇总：胜率、平均收益、最大回撤、对比 ETF 的超额收益

## Capabilities

### New Capabilities

- `golden-pit-stock-backtest`: 黄金坑入坑日对指数成分股运行入场过滤的回测能力，包括成分股获取、日线级入场过滤、14:55 时刻模拟交易、收益对比分析

### Modified Capabilities

<!-- None - this is a new analytical capability, not a change to existing spec behavior -->

## Impact

- 新增文件: `scripts/backtest_golden_pit_stocks.py`（回测主脚本）
- 新增缓存: `data/backtest/指数数据/index_weight/`（成分股历史数据 parquet）
- 参考复用: `backend/app/services/local_data_provider.py`（本地数据读取）、`backend/app/api/indicator.py:2173`（入场过滤逻辑）、`backend/app/services/golden_pit_service.py`（黄金坑指数配置）
- 数据依赖: `golden_pit_snapshots` 表（入坑日期）、`stock_daily.parquet`（日线）、`stock_1min/`（分钟线）、`moneyflow.parquet`（资金流向）、tushare `index_weight` API
- 不影响现有生产系统，纯离线分析脚本
