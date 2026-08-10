## Why

黄金坑窗口内宽基内部板块分化极大：科创50 窗口最强/最弱板块 d30 平均相差 +19.9%（最大 52%），创业板指平均 +17.2%（最大 38%）。直接买入宽基 ETF 只吃到板块均值，错过领涨板块；板块资金流 combo 信号（超跌 oversold120 + 中信二级 5 日资金流 mf5_norm）落地代表 ETF 回测已获正超额：科创50 +2.19%（胜率 73%），创业板指 +5.19%（胜率 64%），现有 SEMI_BOOST_INDICES（588200/512480 各 5%）已验证机制可行但覆盖面窄。因此将两个宽基完全拆分为板块 ETF 组合，宽基仅保留黄金坑择时指导职责，以增强窗口内收益率。

## What Changes

- **BREAKING**：588000（科创50）/159915（创业板指）从"直接买入标的"改为"黄金坑择时指导"——仅保留贪婪值入坑检测、拐点确认、退出信号与 DCA 节奏参考，不再生成宽基本身买入订单。
- 新增板块 ETF 池：中信二级板块 → 代表 ETF 映射（已验证 512480 半导体、588200 科创芯片、515880 通信、512720 计算机、159852 软件、159732 消费电子、515030 新能源、159929 医药、159886 机械、512660 军工等，数据源 tushare fund_daily），可配置扩展。
- 新增坑内板块选择：宽基确认入坑后按 combo_mf_ovs（超跌 + 资金逆势流入）对板块打分排序，选出领涨板块替代固定 SEMI_BOOST 5%+5% 增强。
- 仓位由单宽基改为板块 ETF 组合：`PIT_POSITION_SPLIT` 固定 90/5/5 改为按信号动态分配，保留 `max_total_amount` 总量上限与 DCA 分批节奏。
- 退出保持宽基拐点/贪婪百分位为指导，板块 ETF 按各自信号退出（沿用 `down_turn` 二次拐点退出与 `exit_fallback_days` 兜底机制）。
- 其余宽基（中证500/沪深300/中证1000/恒生/纳指等）逻辑保持不变。

## Capabilities

### New Capabilities
- `golden-pit-sector-etf-split`: 黄金坑窗口内宽基仅作择时指导，板块 ETF 池 + combo 信号选筹 + 动态仓位分配与组合退出

### Modified Capabilities
- `golden-pit-per-index-params`: 588000/159915 语义从可交易指数变为择时指导（不生成买入，`entry/dca` 参数仅作参考），并新增板块 ETF 池与选筹参数配置
- `golden-pit-dca-schedule`: DCA 执行对象从宽基指数变为所选板块 ETF 组合，宽基 `dca_strategy` 仅定义资金投放节奏参考
- `golden-pit-exit`: 退出信号从宽基持仓执行变为"宽基退出指导 + 板块 ETF 各自独立退出"

## Impact

- 代码：`backend/app/services/golden_pit_config.py`（板块池/选筹参数/指导模式）、`golden_pit_service.py`（状态判定与 guide-only 标记）、`golden_pit_dca_service.py`（买入目标解析与仓位分配）、`golden_pit_report.py`（报告展示）。
- 配置：`PIT_POSITION_SPLIT` 替换为动态板块权重，`SEMI_BOOST_INDICES` 并入板块 ETF 池。
- 数据：tushare `fund_daily`（板块 ETF 行情）、中信二级板块资金流（`mf5_norm`）、arkvol 贪婪值。
- 回测资产：`scripts/backtest_golden_pit_sector_*.py` 与 `data/backtest/pit_sector_*.json` 作为参数与阈值来源。
- API：`/golden-pit/status` 兼容保留，588000/159915 增加 `guide_only` 标记，无前端破坏性变更。
