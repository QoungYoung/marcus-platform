# golden-pit-sector-etf-split Specification

## Purpose

在黄金坑窗口内，将科创50（588000）与创业板指（159915）宽基完全拆分为板块 ETF 组合：宽基仅保留择时指导职责（入坑检测、拐点确认、退出信号），坑内资金按 combo 信号（超跌 oversold120 + 中信二级 5 日资金流 mf5_norm）动态选筹并配置到板块 ETF，以增强窗口内收益率。

## ADDED Requirements

### Requirement: 板块 ETF 池配置

系统 SHALL 维护板块 ETF 池（SECTOR_ETF_POOL），以中信二级板块名为键映射代表 ETF（代码、名称、交易所、数据源），作为坑内选筹的候选集合；池配置 SHALL 可扩展，新增映射无需改动代码。

#### Scenario: 初始池包含已验证板块

- **WHEN** 系统初始化板块 ETF 池
- **THEN** 池 SHALL 包含已回测的映射：半导体→512480.SH、科创芯片→588200.SH、通信设备→515880.SH、计算机→512720.SH、软件→159852.SZ、消费电子→159732.SZ、新能源动力系统→515030.SH、生物医药→159929.SZ、机械→159886.SZ、军工→512660.SH

#### Scenario: 池可配置扩展

- **WHEN** 运营在配置中新增板块到代表 ETF 的映射
- **THEN** 系统 SHALL 在下次选筹时将该板块纳入候选集合

#### Scenario: 无 ETF 映射的板块不可选中

- **WHEN** 板块打分 TOP N 中出现未映射代表 ETF 的板块
- **THEN** 该板块 SHALL NOT 进入最终组合

### Requirement: 坑内板块选择信号

系统 SHALL 在宽基确认入坑后，用 combo 信号（超跌 oversold120 + 中信二级 5 日资金流 mf5_norm）对候选板块打分，按分数降序选出 TOP N 板块构成坑内组合。

#### Scenario: 入坑日触发选筹

- **WHEN** 588000 或 159915 状态进入 golden_pit 且满足建仓条件
- **THEN** 系统 SHALL 对板块池内各板块计算 combo 分数（超跌分 + 资金流分）
- **THEN** 系统 SHALL 按 combo 分数降序选取 TOP N（默认 2）板块

#### Scenario: 信号数据不足的板块排除

- **WHEN** 某板块资金流数据缺失或超跌数据不足 120 日
- **THEN** 该板块 combo 分数 SHALL 视为无效并从候选中排除

#### Scenario: 组合为空时保持空仓

- **WHEN** 所有候选板块均未通过 combo 信号门槛
- **THEN** 系统 SHALL NOT 买入任何板块 ETF
- **THEN** 系统 SHALL 在报告中提示"等待板块信号"

### Requirement: 动态仓位分配

系统 SHALL 将宽基 `max_total_amount` 在选中板块 ETF 间动态分配：按 combo 分数归一化权重，单板块权重有上限，累计投入不超过 `max_total_amount`。

#### Scenario: 按分数归一化权重

- **WHEN** 选中两个板块 combo 分数分别为 80 与 20
- **THEN** 两板块权重 SHALL 为 80% 与 20%（归一化后）
- **THEN** 当日买入金额 SHALL = 宽基 DCA 当日权重 × 板块权重 × max_total_amount

#### Scenario: 单板块集中度上限

- **WHEN** 归一化后某板块权重超过单板块上限（默认 50%）
- **THEN** 该板块权重 SHALL 截断至上限
- **THEN** 超额部分 SHALL 按其余板块分数比例再分配

#### Scenario: 总量上限约束

- **WHEN** 组合累计投入接近 max_total_amount
- **THEN** 系统 SHALL 停止新增买入
- **THEN** 累计投入 SHALL NOT 超过 max_total_amount

### Requirement: 宽基 guide_only 标记

系统 SHALL 将 588000/159915 标记为 guide_only：贪婪值入坑检测、拐点确认、退出信号与 ETA 预测照常计算，但 SHALL NOT 生成宽基本身的买入订单。

#### Scenario: 状态接口显示指导模式

- **WHEN** 客户端请求 /golden-pit/status
- **THEN** 588000/159915 对象 SHALL 包含 guide_only=true 及当前选中板块组合摘要

#### Scenario: 不生成宽基买入

- **WHEN** 588000/159915 处于黄金坑且宽基 dca_strategy 计划当日买入
- **THEN** 系统 SHALL NOT 对 588000/159915 本身下单
- **THEN** 当日订单 SHALL 仅针对选中的板块 ETF

### Requirement: 组合退出

系统 SHALL 以宽基退出信号作为组合级指导，同时板块 ETF 按其自身信号独立退出。

#### Scenario: 板块自身二次拐点退出

- **WHEN** 某板块 ETF 触发 down_turn（连续 3 天回落）
- **THEN** 系统 SHALL 清仓该板块 ETF
- **THEN** 其余板块持仓 SHALL 保持不变

#### Scenario: 宽基 full_exit 指导组合清仓

- **WHEN** 588000/159915 触发 full_exit 或 stop_profit
- **THEN** 系统 SHALL 清仓该宽基对应的全部板块 ETF 持仓

#### Scenario: 持有兜底

- **WHEN** 板块 ETF 持仓超过 exit_fallback_days 且未触发其他退出信号
- **THEN** 系统 SHALL 触发 fallback_exit 清仓该持仓
