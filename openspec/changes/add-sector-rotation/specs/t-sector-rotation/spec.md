## Purpose

做T选股的板块轮动增强：将申万一级行业强度并入可T质量评分，过滤弱势行业候选，并在滚动回测模式中支持「汰弱换强」的高切低轮动换仓，使选股在轮动市里能吃到结构行情。

## ADDED Requirements

### Requirement: 行业强度评分因子
系统 SHALL 为做T自动选股（scan）的每个候选计算行业强度分，并按可配权重合并进总评分：final_score = quality × (1 - industry_strength_weight) + industry_strength × industry_strength_weight，默认 industry_strength_weight=0.3。

#### Scenario: 行业强度计算
- **WHEN** 系统计算某候选的行业强度
- **THEN** 系统 SHALL 使用该候选所属申万一级行业（L1）近 5 个交易日的累计涨幅
- **AND** SHALL 将累计涨幅标准化到 [0,1]（涨幅 ≥ 0 映射到正向区间，涨幅 ≤ 0 映射到 ≤ 0.5 的弱区间）
- **AND** SHALL 以标准化值作为 industry_strength 参与加权

#### Scenario: 权重可配置
- **WHEN** 用户设置 industry_strength_weight 为 0 或 1
- **THEN** 总评分 SHALL 分别退化为纯质量分或纯行业强度分
- **AND** 权重 SHALL 被限制在 [0,1] 闭区间

#### Scenario: 生产与回测同口径
- **WHEN** 生产选股（calc_t_quality）与回测选股（build_score）计算同一批候选
- **THEN** 两者的行业强度计算、权重合并公式 SHALL 完全一致
- **AND** 任一侧调整行业因子参数时，另一侧 SHALL 同步生效（共用同一参数默认值）

### Requirement: 行业强势过滤
系统 SHALL 在自动选股时按所属申万一级行业近 5 日累计涨幅过滤候选：累计涨幅 ≤ sector_filter_min_pct（默认 0.0）的候选 SHALL 被排除；sector_filter_enabled（默认 true）可整体关闭过滤。

#### Scenario: 弱势行业排除
- **WHEN** 自动选股扫描一个所属行业近 5 日累计涨幅 ≤ 0 的候选且过滤开启
- **THEN** 该候选 SHALL 不进入候选池
- **AND** 系统 SHALL 记录一条 sector_excluded 事件（含候选、所属行业、行业近 5 日涨幅）用于回放展示

#### Scenario: 震荡市模式同样生效
- **WHEN** 任务以 relax_mode（震荡市）运行且过滤开启
- **THEN** 行业强势过滤 SHALL 仍然生效（放宽的是质量/趋势门槛，不是行业方向）

#### Scenario: 过滤开关
- **WHEN** 用户设置 sector_filter_enabled=false
- **THEN** 行业强势过滤 SHALL 不排除任何候选
- **AND** 行业强度因子 SHALL 仍按权重参与评分（过滤与因子独立开关）

### Requirement: 滚动回测轮动换仓
系统 SHALL 在滚动回测模式（rolling_build / rolling_scan）下支持可配置的轮动换仓（高切低），默认 rotation_enabled=false。

#### Scenario: 持仓弱化触发卖出
- **WHEN** rotation_enabled=true 且某持仓标的满足任一弱化条件（所属行业近 5 日累计涨幅 ≤ sector_filter_min_pct，或自身质量分跌破持仓阈值）
- **THEN** 系统 SHALL 在下一个可执行的交易时段卖出该持仓
- **AND** SHALL 记录一条 sector_switch 事件（注明原因：行业转弱/质量转弱）

#### Scenario: 强势候选换入
- **WHEN** rotation_enabled=true 且候选池存在「行业强度高于被卖出标的且质量达标」的新候选，且距上次同类换仓 ≥ rotation_cooldown_days（默认 2）交易日
- **THEN** 系统 SHALL 用卖出释放的资金买入该候选
- **AND** SHALL 记录一条 sector_switch 事件（含换入标的与原因）

#### Scenario: 冷却期
- **WHEN** 同标的在冷却期内再次触发卖出/换入条件
- **THEN** 系统 SHALL 跳过该次换仓并记录 sector_switch 事件（原因：冷却期内跳过），避免频繁调仓产生过高的交易成本

### Requirement: 行业数据预取与缺数降级
系统 SHALL 预取申万一级行业（L1，31 个）的每日行情（sw_daily）并落盘缓存，回放阶段 SHALL 零网络读取。

#### Scenario: 预取与缓存
- **WHEN** 回测任务创建且自动选股/行业因子启用
- **THEN** 系统 SHALL 在任务执行前拉取回测区间内每个交易日全部申万一级行业的日线
- **AND** SHALL 落盘为按交易日组织的行业缓存，回放阶段从缓存读取

#### Scenario: 行业数据缺失降级
- **WHEN** 某交易日行业数据缺失（拉取失败/缓存缺失）
- **THEN** 该日的行业过滤 SHALL 降级跳过（不排除任何候选）
- **AND** 行业强度因子 SHALL 对该日候选取中性值（0.5）
- **AND** 系统 SHALL 在 caliber_notes 与事件流中标注该日行业数据缺失，保证结果可审计
