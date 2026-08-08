## ADDED Requirements

### Requirement: 防御组合纳入贪婪监测
系统 SHALL 将防御组合标的（红利 510880、银行 512800、黄金 518880、国债 511010、有色 512400）纳入黄金坑贪婪监测。每个标的 SHALL 使用 ArkVol 贪婪值（fund_code：009052/014028/020412/020741/017193）作为展示与快照数据源，并使用 250 日滚动价格分位作为入坑/撤场信号判定依据。

#### Scenario: 防御标的状态出现在状态接口
- **WHEN** 客户端请求 GET /golden-pit/status
- **THEN** 响应 indices 列表 SHALL 包含 5 个防御标的
- **THEN** 每个防御标的 SHALL 包含 fund_code/index_name/greed/percentile/status/exit_signal 字段
- **THEN** 防御标的的 tier SHALL 为 "defense_rotation"

#### Scenario: ArkVol 历史不足回退价格分位
- **WHEN** 防御标的的 ArkVol 贪婪历史不足 60 天
- **THEN** 系统 SHALL 使用 250 日价格分位合成贪婪值判定状态
- **THEN** ArkVol 贪婪值 SHALL 仅用于展示与快照采集

### Requirement: 防御组合入坑与撤场阈值
系统 SHALL 按回测校准的独立阈值判定防御标的入坑与撤场：
- 红利：价格分位 ≤P20 入坑，≥P40 撤场
- 银行：≤P10 入坑，≥P40 撤场
- 黄金：≤P15 入坑，≥P50 撤场
- 国债：≤P10 入坑，≥P50 撤场
- 有色：不触发入坑信号（仅作为防御组合成分）

#### Scenario: 红利价格分位触底入坑
- **WHEN** 红利 510880 的 250 日价格分位 ≤ 20
- **THEN** 系统 SHALL 将该标的 status 置为 "golden_pit"
- **THEN** 状态字段 SHALL 携带入坑阈值 P20

#### Scenario: 红利价格分位回升撤场
- **WHEN** 红利 510880 已入坑且价格分位 ≥ 40
- **THEN** 系统 SHALL 触发 exit_signal="full_exit"
- **THEN** exit_reason SHALL 引用价格分位阈值 P40

#### Scenario: 有色不产生入坑信号
- **WHEN** 有色 512400 的价格分位处于极端低位（≤P5）
- **THEN** 系统 SHALL NOT 将该标的置为 golden_pit
- **THEN** 有色 SHALL 保持 normal 状态并作为防御组合成分展示

### Requirement: 半导体增强标的贪婪监测
系统 SHALL 通过 ArkVol tech-hardware-greed 接口（/api/tech-hardware-greed/series?days=365）监测 588200（科创芯片）与 512480（半导体）的贪婪值，作为坑内增强仓位的自身入坑/撤场信号源。

#### Scenario: 588200/512480 贪婪值接入
- **WHEN** 系统从 tech-hardware-greed 接口获取数据
- **THEN** 588200 与 512480 的状态 SHALL 使用接口返回的 greed 值
- **THEN** 两者的贪婪历史 SHALL 写入 golden_pit_snapshots 快照

#### Scenario: 半导体标的自身入坑判定
- **WHEN** 588200 贪婪值进入自身配置的入坑阈值
- **THEN** 系统 SHALL 将该标的状态置为 golden_pit 或 warning
- **THEN** 该状态 SHALL 用于 DCA 增强仓位（10%）的买入条件