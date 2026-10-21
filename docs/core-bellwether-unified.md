# 统一风向标每日流水线（45 子主题）

> 2026-10-21；一个调度任务替代 proxy/agent/state 三个旧任务。

## 任务

```text
core_bellwether_daily
每日 18:30（chain_map_daily 之后）
```

## 步骤

```text
1. proxy 观察：jobs/build_core_p05.py --latest-only
2. agent 确认：jobs/agent_confirm_bellwethers.py
   - 读原始日线 + chain_map
   - 不读 proxy
3. 全 45 状态：jobs/build_bellwether_state.py --history
4. 权限上下文：data/core_p1/context_latest.json
```

## 输出

- `data/core_p05/confirmed_daily.json`
- `data/core_p05/confirmed_changes.csv`
- `data/core_p1/bellwether_state_latest.json`
- `data/core_p1/bellwether_history.csv`
- `data/core_p1/context_latest.json`
- `data/core_p1/daily_pipeline_latest.json`

## 旧任务

已停用（保留手动触发）：

- `core_p05_bellwether_refresh`
- `core_p05_confirmed_refresh`
- `core_p05_agent_confirm`
- `core_p1_bellwether_state`

## 注意

- 确认、状态、权限对 45 个子主题用同一套规则；
- 非科技数据质量/验证成熟度仍低于科技，输出中保留 `state`，不因此分叉规则；
- agent 失败时 confirmed 不变，pipeline 记录失败。
