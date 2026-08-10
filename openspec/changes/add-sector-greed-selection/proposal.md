## Why

当前黄金坑板块拆分选筹（combo = 超跌 oversold120 + 中信二级 5 日资金流 mf5_norm）在生产回测中暴露出两个问题：一是拐点确认当天资金流信号经常不足（有效信号 < min_valid），导致频繁空仓/回退，错过坑内行情；二是大牛坑里资金流选出的板块跑输宽基（2024-07 创业板 +25.3% vs +61.9%，2024-09 科创 +37.2% vs +55.4%）。回测验证改用「超跌 + 板块贪婪」（arkvol funds-greed）后，2025-01 后窗口组合收益显著提升（科创 +12.41% vs 资金流版 +11.0%，创业板 +6.47% vs +3.53%），且当前 2026-08 坑选筹成功（资金流版失败）。需要将该信号模式接入生产选筹服务。

## What Changes

- 板块选筹信号模式支持配置切换：新增 `signal_mode` 配置（`greed` 默认 / `moneyflow` 旧逻辑可回滚），灰度期可经黄金坑配置弹窗动态切换
- `greed` 模式选筹：有效信号 = 超跌中（oversold120 < 0）且板块贪婪可查；combo = -(rank(greed 升序) + rank(oversold120 升序))；min_valid / TOP N / 权重归一化逻辑沿用现有实现
- 新增板块贪婪数据源：SECTOR_ETF_POOL 每板块增加 arkvol 贪婪代表基金映射（greed_code），经 arkvol `funds-greed/fund` 接口拉取历史贪婪（2025-01 起），服务内缓存
- 数据不足处理：贪婪数据不可用的板块自动排除；所有板块均不可用或有效信号不足时保持现有「空仓等待」行为（DCA 跳过买入，schedule_day 不递增）
- 退出、DCA 金额分配、状态/报告展示逻辑不变（select_sectors 返回结构保持不变）

## Capabilities

### New Capabilities
- 无

### Modified Capabilities
- `golden-pit-sector-etf-split`: 坑内板块选择信号从单一「超跌+资金流」扩展为可配置双模式（默认 greedy），并新增板块贪婪数据源需求

## Impact

- `backend/app/services/golden_pit_sector_service.py`：combo 计算分支、贪婪序列加载与缓存、配置读取（signal_mode）
- `backend/app/services/golden_pit_config.py`：SECTOR_ETF_POOL 增加 greed_code 字段；新增 `SECTOR_SIGNAL_MODE` 默认配置
- `backend/app/config.py` / `.env`：新增 `GOLDEN_PIT_SECTOR_SIGNAL_MODE` 配置项（默认 greed）
- `backend/app/services/golden_pit_sector_service.py` 配置表/黄金坑配置弹窗：新增 `signal_mode` 配置项（DB 可覆盖）
- 依赖：arkvol funds-greed/fund 接口（需 ARKVOL_API_KEY/ARKVOL_COOKIE，已配置）
- 调用方（DCA 450/1474、golden_pit_service._attach_sector_split、报告）无接口变化
- 回测脚本 `scripts/backtest_sector_greed_500d.py` 作为信号口径参照保留
