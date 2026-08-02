# golden-pit-dca-schedule Delta Specification

## Purpose

DCA 执行时间从固定 10:05 改为按 per-index 配置的分时执行。

## ADDED Requirements

### Requirement: DCA 分时执行

系统 SHALL 将 DCA 定投任务的触发时间从单一固定时间（10:05）改为按指数配置的 `buy_time` / `buy_time_pit` 两个时点（早盘 09:36、尾盘 14:44）分批次执行。同一指数每天最多执行一次。

#### Scenario: 多指数跨批次执行

- **WHEN** 当日需要买入 4 个指数：中证500（buy_time=09:36）、创业板指（buy_time=09:36）、科创50（buy_time_pit=09:37）、市场300（buy_time=14:44）
- **THEN** 早盘批次（09:36）SHALL 执行中证500、创业板指、科创50
- **THEN** 尾盘批次（14:44）SHALL 执行沪深300
- **THEN** 每个指数仅执行一次

#### Scenario: 已执行指数不重复

- **WHEN** 中证500 在早盘批次（09:36）已执行
- **WHEN** 尾盘批次（14:44）触发
- **THEN** 中证500 SHALL NOT 再次执行
- **THEN** 系统 SHALL 通过查询 DCA 日志中的 `buy_day` 判重
