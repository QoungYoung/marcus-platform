## Why

当前黄金坑 DCA 只覆盖 18 个指数 + tech7/prod10 板块选筹，监测面窄导致资金大量时间闲置：坑与坑之间的空窗期（尤其牛市）现金趴账，全市场 30+ 行业（创新药/白酒/光伏/券商/新能源车等）虽有贪婪数据却不参与 DCA。目标是把"等单个坑"升级为"等任一行业坑"，用全行业监测 + 坑位冲突优先级资金池裁决，让资金在坑间持续轮动，同时保留现金下限防止摊大饼。

## What Changes

- 新增**全行业监测池**：从 arkvol `funds-greed/sectors`（30+ 行业）挑选有 ETF/场外净值代理且有贪婪历史的行业，逐行业计算 250 日贪婪分位 + N 日超跌，作为独立 DCA 触发信号。
- 新增**资金池优先级裁决**：每日汇总所有 in-pit 行业当日计划定投金额，若总和超过可用现金，按（tier 优先级，行业 priority，当前仓位权重）比例裁剪，低优先级让位高优先级。
- 新增**现金下限与分级限仓**：账户保留 X% 现金作抄底弹药；每个行业 `max_total` 分级（核心/卫星/防御），单行业上限防梭哈。
- 复用**坑间防御承接**：行业出场资金轮入防御组合（红利/黄金/国债/有色），新坑触发时从防御赎回回补（现有 `_sell_defense_on_reentry` 机制扩展至全行业）。
- **BREAKING**：DCA 触发从"指数级信号"扩展为"指数 + 行业双轨"，`golden_pit_dca_service` 调度需支持任意标的列表；配置集中到 `golden_pit_etf_config` / `golden_pit_sector_config`（优先级、max_total、现金下限、启用开关）。

## Capabilities

### New Capabilities
- `industry-dca-priority-pool`: 全行业监测、坑位冲突优先级资金池裁决、现金下限与分级限仓、坑间防御轮动承接（含出场→防御→新坑赎回回补的完整资金流转）。

### Modified Capabilities
- `golden-pit-dca-schedule`: DCA 触发源从固定指数扩展为可配置行业池；权重/兜底/进度逻辑保持，触发判定增加行业贪婪分位+超跌双条件。
- `golden-pit-per-index-params`: 新增行业级配置项（priority、max_total、min_days_in_pit、现金贡献权重），与现有指数参数同表集中管理。
- `portfolio`: 新增资金池视图（可投现金/在坑标的计划金额/裁剪结果/现金下限），供页面与报告展示。

## Impact

- 后端：`golden_pit_dca_service.py`（调度主循环 + 资金池裁决）、`golden_pit_sector_service.py`（行业贪婪/超跌信号复用）、`golden_pit_tech_status.py`（全行业现状）、新增行业池配置（`golden_pit_config.py`）。
- 数据：arkvol `funds-greed/sectors` + `funds-greed/fund/{code}`（贪婪历史 2025-01 起）；tushare `fund_daily`/`fund_nav`（行业代理价格）。
- 数据库：`golden_pit_etf_config`（新增行业行）、`golden_pit_sector_config`（新增 pool/资金池配置键）、`golden_pit_dca_log`（strategy 增加 `industry/...` 前缀，向后兼容）。
- 前端：黄金坑页新增"全行业监测 + 资金池"面板（配置弹窗复用）。
- 风险：行业贪婪历史仅 2025-01 起（约 19 个月），全行业回测样本短，结论需标注过拟合风险。
