# tech7-sector-selection Specification

## Purpose

生产黄金坑板块拆分采用 tech7 选筹池：7 只场内科技 ETF 作为默认候选池，板块贪婪数据源切换为 arkvol `tech-hardware-greed/series`，替代存在数据停更问题的原 10 板块 `funds-greed/fund` 池，保留 greed 选筹、回退宽基与出场机制不变。

## ADDED Requirements

### Requirement: tech7 板块池定义

系统 SHALL 将生产黄金坑板块拆分的默认候选池定义为 tech7，包含 7 只场内科技 ETF：创业板50(159949)、半导体(512480)、人工智能(512930)、5G通信(515050)、大数据(515400)、通信设备(515880)、科创芯片(588200)，其中 512480/515880/588200 与既有池重叠。

#### Scenario: 池内标的与代码

- **WHEN** 系统初始化板块候选池
- **THEN** 候选池 SHALL 仅包含 tech7 的 7 只场内 ETF（6 位代码 + 交易所前缀）
- **AND** 每只标的 SHALL 可被选筹、计算超跌与贪婪信号

#### Scenario: 原池标的移除

- **WHEN** tech7 池作为生产默认池启用
- **THEN** 原池中不在 tech7 的标的（计算机 512720、软件 159852、消费电子 159732、新能源动力系统 515030、生物医药 159929、机械 159886、军工 512660）SHALL 不作为默认候选参与选筹

### Requirement: tech-hardware 贪婪数据源

系统 SHALL 从 arkvol `tech-hardware-greed/series` 接口加载板块贪婪历史（date → greed），作为 tech7 池 greed 信号模式的唯一输入；加载结果 SHALL 按服务缓存策略缓存（TTL 与现有贪婪缓存一致）。

#### Scenario: 加载 7 只标的贪婪序列

- **WHEN** 系统需要计算板块贪婪信号
- **THEN** 系统 SHALL 通过 arkvol `fetch_tech_greed(days=2000)` 拉取 tech-hardware 序列
- **AND** 从返回的 `data` 中提取 tech7 对应 6 位代码的 {date: greed} 映射
- **AND** 贪婪历史缺失时该标的当日视为无值

#### Scenario: 贪婪数据不可用降级

- **WHEN** tech-hardware 接口返回空、请求失败或某标的当日无贪婪值
- **THEN** 该标的在 greed 模式下 SHALL 视为无有效信号并从候选中排除
- **AND** 其他标的选筹 SHALL 不受影响
- **AND** 有效信号不足 `min_valid` 时 SHALL 保持空仓并报告等待板块信号

### Requirement: 生产默认池与回滚配置

系统 SHALL 支持板块池来源配置：默认 `tech7`，可切换回原 10 板块池（`prod10`）以回滚；配置 SHALL 支持代码常量默认值及黄金坑配置表（DB）动态覆盖。

#### Scenario: 默认启用 tech7

- **WHEN** 系统初始化且未显式配置池来源
- **THEN** 生产选筹 SHALL 使用 tech7 池
- **AND** 贪婪信号 SHALL 使用 tech-hardware 数据源

#### Scenario: 回滚到原池

- **WHEN** 配置池来源为 `prod10`
- **THEN** 选筹 SHALL 恢复原 10 板块池与 `funds-greed/fund` 贪婪数据源
- **AND** 回滚 SHALL 无需改代码，仅需改配置并重启/刷新

### Requirement: 选筹与回退行为延续

tech7 池 SHALL 沿用既有 greed 选筹机制：有效信号 = 超跌中（`oversold120 < 0`）且当日贪婪可查；`combo = -(rank(greed 升序) + rank(oversold120 升序))`；按 combo 降序取 TOP N（默认 2）并归一化权重（单标的权重上限 `max_weight`）。

#### Scenario: 坑内拐点日选筹

- **WHEN** 588000 或 159915 入坑且拐点确认
- **THEN** 系统 SHALL 对 tech7 池内各标的计算 combo 分数
- **AND** 按分数降序选取 TOP N 标的构成坑内组合
- **AND** 组合权重 SHALL 归一化且单标的不超过 `max_weight`

#### Scenario: 有效信号不足回退宽基

- **WHEN** 有效信号板块数 < `min_valid`
- **THEN** 系统 SHALL 回退买入宽基（588000/159915）
- **AND** 回退持仓期间坑内后续拐点确认日重新选筹，成功则切回板块

#### Scenario: 板块出场规则不变

- **WHEN** 宽基信号触发全仓止盈（P70/P80）、半仓（P40）、兜底（20/25 天）或板块连 3 日回落
- **THEN** 出场行为 SHALL 与既有生产规则一致

## MODIFIED Requirements

### Requirement: 坑内板块选择信号

系统 SHALL 在宽基确认入坑后，按当前 `signal_mode` 用 combo 信号对**当前池（tech7 或 prod10）**内板块打分，按分数降序选出 TOP N 板块构成坑内组合。greed 模式：有效信号 = 超跌中（oversold120 < 0）且板块贪婪可查，combo = -(rank(greed 升序) + rank(oversold120 升序))；moneyflow 模式：有效信号 = 超跌中且 mf5_norm > 0，combo = -(rank(mf5_norm 降序) + rank(oversold120 升序))。

#### Scenario: 入坑日触发选筹

- **WHEN** 588000 或 159915 状态进入 golden_pit 且满足建仓条件
- **THEN** 系统 SHALL 按当前池与当前 signal_mode 对池内各板块计算 combo 分数
- **AND** 系统 SHALL 按 combo 分数降序选取 TOP N（默认 2）板块

#### Scenario: 信号数据不足的板块排除

- **WHEN** 某板块所需信号数据缺失（greed 模式无贪婪值 / moneyflow 模式无资金流）或超跌数据不足 120 日
- **THEN** 该板块 combo 分数 SHALL 视为无效并从候选中排除

#### Scenario: 组合为空时保持空仓

- **WHEN** 所有候选板块均未通过 combo 信号门槛
- **THEN** 系统 SHALL NOT 买入任何板块 ETF
- **THEN** 系统 SHALL 在报告中提示"等待板块信号"
- **AND** DCA SHALL 跳过当日板块买入且 schedule_day 不递增
