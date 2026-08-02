# golden-pit-intraday-timing Specification

## Purpose

基于分钟级历史数据，为每个黄金坑 ETF 配置日内最优买入时间，区分普通交易日和黄金坑日，以优化 DCA 买入的执行价格。

## ADDED Requirements

### Requirement: Per-index 日内买入时间配置

系统 SHALL 在 `CHINA_INDICES` 的每个指数配置中支持 `buy_time` 和 `buy_time_pit` 两个可选字段，分别指定普通交易日和黄金坑日的推荐买入时间（格式 `HH:MM`），精确到分钟。

#### Scenario: 配置了 buy_time 和 buy_time_pit 的指数

- **WHEN** 科创50 配置 `buy_time="14:44"`, `buy_time_pit="09:37"`
- **WHEN** 当前为黄金坑日（days_in_pit > 0）
- **THEN** 系统 SHALL 使用 `buy_time_pit`（09:37）作为目标执行时间
- **WHEN** 当前为普通日（days_in_pit == 0）
- **THEN** 系统 SHALL 使用 `buy_time`（14:44）作为目标执行时间

#### Scenario: 仅配置了 buy_time

- **WHEN** 中证500 配置 `buy_time="09:36"`，`buy_time_pit` 未设置
- **THEN** 无论是否为黄金坑日，系统 SHALL 使用 `buy_time="09:36"`

#### Scenario: 两个字段均未配置

- **WHEN** 某指数未配置 `buy_time` 和 `buy_time_pit`
- **THEN** 系统 SHALL 使用系统默认值 `09:36`

### Requirement: 分时触发器

系统 SHALL 通过两个 APScheduler 触发器（早盘 09:36、尾盘 14:44）调用同一 DCA 执行函数。函数内部 SHALL 根据当前时间和各指数的目标执行时间过滤，仅执行时间匹配的指数。

#### Scenario: 早盘批次执行

- **WHEN** 09:36 触发器触发
- **THEN** 系统 SHALL 仅执行 `buy_time` 或 `buy_time_pit` 为 `09:36`（含 `09:35-09:40` 窗口内）的指数
- **THEN** `buy_time` 为 `14:44` 的指数 SHALL NOT 在此批次执行

#### Scenario: 尾盘批次执行

- **WHEN** 14:44 触发器触发
- **THEN** 系统 SHALL 仅执行 `buy_time` 或 `buy_time_pit` 为 `14:44`（含 `14:15-14:55` 窗口内）的指数
- **THEN** 已在早盘批次执行的指数 SHALL NOT 重复执行

#### Scenario: 某批次无可执行指数

- **WHEN** 09:36 触发器触发，但所有指数的 `buy_time` 均为 `14:44`
- **THEN** 系统 SHALL 正常结束，不报错
- **THEN** DCA 日志 SHALL 当天无新增记录

### Requirement: 买入时间在日志中可审计

系统 SHALL 在 DCA 日志的 `strategy` 字段中记录实际使用的买入时间配置。

#### Scenario: 日志包含买入时间

- **WHEN** 科创50 在黄金坑日以 `buy_time_pit="09:37"` 执行
- **THEN** DCA 日志的 `strategy` 字段 SHALL 包含 `time=09:37` 或等效标记
