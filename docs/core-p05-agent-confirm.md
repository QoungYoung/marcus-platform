# DSH agent 每日确认风向标

> 2026-10-21；确认来源 = 原始日线 + chain_map；**不读 proxy**。

## 每日链路

```text
18:30  proxy 仅观察
18:35  科技状态
18:40  proxy 确定性兜底（已停用）
18:45  DSH agent 代人工确认
```

## Agent 输入

`data/core_p05/agent_review_<date>.json`：

- 子主题成分
- 当前 confirmed
- 原始候选：close/high/amount、r5/r20、20日新高、chain_map role
- 不含 proxy score/top5

## 决策

```text
默认 keep
switch 条件：
  同子主题/链段
  + 创新高
  + 5日相对旧风向标强
  + 旧风向标走弱
  + 广度没崩
```

switch 目标必须在 `raw_candidates`；否则拒绝。

## 输出

- `data/core_p05/agent_review_<date>.json`
- `data/core_p05/agent_confirm_<date>.json`
- `data/core_p05/confirmed_daily.json`
- `data/core_p05/confirmed_changes.csv`

## 当前实测（20261020）

Agent 原始数据复核 45 个子主题，2 个 switch：

- 基建：中国能建 → 中国建筑（新高、5日相对强、能建走弱）
- 电网设备：精达股份 → 平高电气（精达 5日 -17.66%，平高新高）

其余 keep。
