## ADDED Requirements

### Requirement: 阈值穿越预警

系统 SHALL 每日检测各指数 percentile 是否穿越 P10（预警线）与 P5（黄金坑线）。当从高于阈值穿越到不高于阈值时，系统 SHALL 生成预警消息；从低于阈值反弹回高于阈值 SHALL NOT 生成预警。

#### Scenario: P10 预警线穿越
- **WHEN** 某指数前一日 percentile=12，当日 percentile=9
- **THEN** 系统 SHALL 生成该指数进入预警区的预警消息

#### Scenario: P5 黄金坑确认
- **WHEN** 某指数前一日 percentile=6，当日 percentile=4
- **THEN** 系统 SHALL 生成该指数进入黄金坑的预警消息，并附带窗口起止日期与预期收益

#### Scenario: 反弹不预警
- **WHEN** 某指数前一日 percentile=8，当日 percentile=11
- **THEN** 系统 SHALL NOT 生成预警消息

### Requirement: 对比基准为最近交易日快照

系统 SHALL 以"今天之前最近一个存在快照的交易日"作为穿越检测的对比基准，而非固定取昨天/前天的自然日。当数据库中不存在早于今天的快照时 SHALL 跳过全部指数的检测。

#### Scenario: 周一检测上周五快照
- **WHEN** 周一执行盘前检查，且最近快照日期为上周五
- **THEN** 系统 SHALL 以上周五的 percentile 作为对比基准，正常生成穿越预警

#### Scenario: 节后首日检测节前快照
- **WHEN** 长假后首个交易日执行检查，最近快照日期为节前最后交易日
- **THEN** 系统 SHALL 以节前最后交易日的 percentile 作为对比基准，正常生成穿越预警

#### Scenario: 无历史快照时跳过
- **WHEN** 数据库中不存在任何早于今天的快照
- **THEN** 系统 SHALL 跳过全部指数的穿越检测，不产生预警

