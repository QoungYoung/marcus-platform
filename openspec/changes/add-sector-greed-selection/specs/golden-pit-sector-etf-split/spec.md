# golden-pit-sector-etf-split Specification

## Purpose

在黄金坑窗口内，将科创50（588000）与创业板指（159915）宽基完全拆分为板块 ETF 组合：宽基仅保留择时指导职责（入坑检测、拐点确认、退出信号），坑内资金按 combo 信号动态选筹并配置到板块 ETF，以增强窗口内收益率。combo 信号支持可配置双模式：默认「超跌 + 板块贪婪」（greed），并保留「超跌 + 中信二级 5 日资金流」（moneyflow）作为回滚选项。

## ADDED Requirements

### Requirement: 板块贪婪数据源

系统 SHALL 从 arkvol `funds-greed/fund` 接口加载板块代表基金的贪婪历史序列（date → greed），作为 greed 信号模式的输入；加载结果 SHALL 按服务缓存策略缓存（TTL 与现有行情缓存一致）。

#### Scenario: 板块池配置贪婪代表基金

- **WHEN** SECTOR_ETF_POOL 中的板块配置了 `greed_code`（arkvol 代表基金代码）
- **THEN** 系统 SHALL 通过 arkvol `fetch_fund_series(greed_code, days=2000)` 拉取该板块贪婪历史
- **AND** 贪婪序列 SHALL 以 {date: greed} 映射缓存，供 as_of 当日选筹使用

#### Scenario: 贪婪数据不可用

- **WHEN** 某板块贪婪接口返回空、请求失败或当日无贪婪值
- **THEN** 该板块在 greed 模式下 SHALL 视为无有效信号并从候选中排除
- **AND** 其他板块选筹 SHALL 不受影响

### Requirement: 信号模式配置

系统 SHALL 通过 `signal_mode` 配置项控制板块选筹信号维度，取值 `greed`（默认）或 `moneyflow`；配置 SHALL 支持 .env 默认值与黄金坑配置表（DB）动态覆盖。

#### Scenario: 默认 greedy 模式

- **WHEN** 系统初始化且未显式配置 signal_mode
- **THEN** signal_mode SHALL 为 `greed`
- **AND** 选筹 SHALL 使用「超跌 + 板块贪婪」维度

#### Scenario: 切换到 moneyflow 模式

- **WHEN** 配置 signal_mode 为 `moneyflow`
- **THEN** 选筹 SHALL 恢复「超跌 + 中信二级 5 日资金流」逻辑
- **AND** 资金流数据缺失时行为 SHALL 与既有实现一致

## MODIFIED Requirements

### Requirement: 坑内板块选择信号

系统 SHALL 在宽基确认入坑后，按当前 `signal_mode` 用 combo 信号对候选板块打分，按分数降序选出 TOP N 板块构成坑内组合。greed 模式：有效信号 = 超跌中（oversold120 < 0）且板块贪婪可查，combo = -(rank(greed 升序) + rank(oversold120 升序))；moneyflow 模式：有效信号 = 超跌中且 mf5_norm > 0，combo = -(rank(mf5_norm 降序) + rank(oversold120 升序))。

#### Scenario: 入坑日触发选筹

- **WHEN** 588000 或 159915 状态进入 golden_pit 且满足建仓条件
- **THEN** 系统 SHALL 按当前 signal_mode 对板块池内各板块计算 combo 分数（greed 模式为超跌分 + 板块贪婪分；moneyflow 模式为超跌分 + 资金流分）
- **THEN** 系统 SHALL 按 combo 分数降序选取 TOP N（默认 2）板块

#### Scenario: 信号数据不足的板块排除

- **WHEN** 某板块所需信号数据缺失（greed 模式无贪婪值 / moneyflow 模式无资金流）或超跌数据不足 120 日
- **THEN** 该板块 combo 分数 SHALL 视为无效并从候选中排除

#### Scenario: 组合为空时保持空仓

- **WHEN** 所有候选板块均未通过 combo 信号门槛
- **THEN** 系统 SHALL NOT 买入任何板块 ETF
- **THEN** 系统 SHALL 在报告中提示"等待板块信号"
- **AND** DCA SHALL 跳过当日板块买入且 schedule_day 不递增
