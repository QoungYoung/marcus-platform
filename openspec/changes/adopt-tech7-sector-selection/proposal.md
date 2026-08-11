## Why

生产板块选筹池（`SECTOR_ETF_POOL` 10 板块 + arkvol `funds-greed/fund` 贪婪）存在数据源停更问题：计算机/软件贪婪停在 2026-07-09、多只场外代表基金滞后 1 个交易日，导致有效信号常不足 `min_valid=4` 而空仓；同时按生产 500 天分位口径回测，arkvol `tech-hardware-greed` 覆盖的 7 只场内科技 ETF 池在 2025 年以来 5 个黄金坑板块窗口全部跑赢宽基（超额平均 +5.86%、5/5 胜率），优于生产池（超额平均 +3.21%、3/5）。生产应采用更优且数据更新及时的 tech7 选筹池。

## What Changes

- 生产板块选筹池从 10 板块 `SECTOR_ETF_POOL` 切换为 tech7 7 只场内科技 ETF：创业板50(159949)、半导体(512480)、人工智能(512930)、5G通信(515050)、大数据(515400)、通信设备(515880)、科创芯片(588200)
- 板块贪婪数据源从 `funds-greed/fund` 切换为 `tech-hardware-greed/series`（7 只全覆盖、更新至当日）
- 移除原池中数据停更/属性不匹配标的：计算机、软件、消费电子、新能源动力系统、生物医药、机械、军工（航天航空与同源 588080 已在候选验证中剔除）
- 保留既有机制不变：greed 信号（超跌 `oversold120<0` + 当日贪婪可查）、combo 排序 TOP N、`min_valid` 空仓等待、选筹失败回退宽基、回退后拐点日切回板块、出场规则（P70/P80 全仓、P40 半仓、兜底 20/25 天、板块连 3 日回落提前卖）
- 配置表 `golden_pit_sector_config` 的 `signal_mode` 等项保持兼容；新增板块池来源控制（代码常量默认 + 表配置可覆盖）

## Capabilities

### New Capabilities
- `tech7-sector-selection`: 定义 tech7 板块池（7 只场内科技 ETF）及其 tech-hardware 贪婪数据源，作为生产黄金坑板块拆分的默认选筹池，覆盖选筹、回退、降级与配置行为

### Modified Capabilities
<!-- 主 specs 中无 golden-pit-sector-etf-split（该能力目前仅存在于 add-sector-greed-selection 变更的 delta spec），板块池替换由新 capability tech7-sector-selection 承接 -->

## Impact

- `backend/app/services/golden_pit_config.py`：`SECTOR_ETF_POOL` 替换/新增 tech7 池定义与贪婪数据源标记
- `backend/app/services/golden_pit_sector_service.py`：板块贪婪加载 `_load_sector_greed_map` 切换/兼容 tech-hardware 源；选筹输入与降级逻辑
- `golden_pit_sector_config`（PostgreSQL）：若支持池来源切换则新增配置项；`signal_mode` 语义不变
- 前端黄金坑页面：板块池/选筹展示文案随数据源更新（如需）
- 回测脚本（`scripts/backtest_sector_prod_500d.py`、`data/backtest/_pool_merge_backtest.py`）与文档 `docs/golden-pit-sector-etf-report.md`
- 依赖：arkvol `tech-hardware-greed/series` API（登录态已在 .env 具备）
