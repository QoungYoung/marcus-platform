# v3 止损生产接线（dsh 容器版）

> 目标：把“窗口快照 → dsh agent 判定 → 校验 → 卖出计划 → gateway 下单”接起来。
> 默认 **WOLF_V3_LIVE=0**（只出计划/日志）；=1 才真正调 `gateway_execute`。

## 1. 新增文件

- `backend/app/services/wolf_v3_production.py`
  - `build_snapshot()`：PIT 快照（phase/last_price/partial OHLC/候选）
  - `build_prompt()`：给 agent 的 prompt（含窗口、触发线/支撑线规则）
  - `ask_agent()`：POST 生产 dsh 容器 `/chat`
  - `validate_decision()`：校验候选 id/价格/状态/`risk_trigger`，拒绝发明价格
  - `plan_sell()`：按 `target_pct` + 可卖 + 底仓 floor 算卖出量
  - `execute_plan()`：`WOLF_V3_LIVE=1` 时调 `gateway_execute`
  - `action_key/already_done/record_action`：JSONL 幂等
- `backend/tests/test_wolf_v3_production.py`（6 用例）
- `backend/app/services/t_monitor.py`：`_check_stop_loss_v3()` 加 tail_confirm live hook
- `backend/app/services/decision_window.py`：14:50/14:55/15:00 窗口协议

## 2. 生产流程

```
t_monitor 每轮
  → phase_at(now)
  → 仅 phase=tail_confirm（14:55）+ dec.action in (reduce_to, exit)
  → build_snapshot(symbol, quote, bars)
  → ask_agent(dsh /chat)
  → validate_decision()
  → plan_sell(target_pct)
  → already_done(key)?
  → execute_plan()
      WOLF_V3_LIVE=0 → planned（只日志）
      WOLF_V3_LIVE=1 → gateway_execute(is_stop_loss=True, kind="wolf_v3_stop")
```

## 3. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `WOLF_V3_LIVE` | `0` | 1 才真下单 |
| `WOLF_V3_AGENT_URL` | `http://marcus-dsh:3001/chat` | 生产 dsh 容器 |
| `WOLF_V3_AGENT_TIMEOUT` | `45` | agent 超时秒 |
| `DATA_DIR` | `data` | `wolf_v3_actions.jsonl` 落盘 |

## 4. 已验证

- 快照 phase=tail_confirm、last_price、partial low；不把 14:55 当官方收盘；
- agent JSON 解析、未知候选/价格不符/`risk_trigger != break_trigger` 拒绝；
- `target_pct=0.30` + 可卖 10000 + floor 2000 → 卖 7000；
- 默认 `WOLF_V3_LIVE=0` 只返回 `planned`；
- 30 个相关测试通过（v3 production 6 + stop layer + window + monitor v3）。

## 5. 还没接的（必须先知道）

1. **只接了 tail_confirm**：14:30 定点、3×15、放量即时还没进 live hook。
2. **只在 `WL.evaluate()` 触发后才问 agent**：如果确定性层 `hold`，agent 不会被问；B/C/E 这类“他的第一档”可能漏。
3. **快照只有日线**：`t_monitor` 还没把 5m/15m 实时喂给 `build_snapshot`，5m 候选在生产里暂时没有。
4. **没有 Case/ranker**：agent 仍可能选 3835 而不是 3922；先不要全量放开。
5. **没有 14:50 预计算**：45s agent 调用压在 14:55，有尾盘来不及风险；应 14:50 预生成、14:54 缓存。
6. **幂等是 JSONL 单文件**：单 worker 够用；多进程/重启需要改 PG 表。
7. **没有单日最大卖出/单票上限**：目前只靠 gateway 的底仓/可卖/时点门。

## 6. 上线顺序

1. 先 `WOLF_V3_LIVE=0` 跑 1 天，看 `stop_v3_events.jsonl` 的 `agent_ok/plan`；
2. 再 `WOLF_V3_LIVE=1` + 单票小额（手工把 target_pct 限在 0.5 以上，先卖 T 仓）；
3. 14:50 预计算补上后再放开尾盘；
4. Case/ranker 未定时，不让 agent 单独决定第一档；只做“是否执行”的第二意见。

一句话：**生产管线已经能跑通，但当前只覆盖 tail_confirm 的“计划/小额执行”；Case 第一档、5m 实时、14:50 预计算、PG 幂等还没补，不能直接全量放开。**

---

## 7. 2026-10-24 补完并上线

已补：

- **14:30 定点 + 3×15 合并到 14:30 问 agent**（13:00-14:30 是执行禁区，统一挪到 14:30）；
- **always-ask**：`_v3_agent_positions()` 不再依赖 `WL.evaluate()` 是否 hold，tail_confirm/14:30 全持仓都问；
- **5m/15m 实时**：`_v3_intraday_bars()` 走 `fetch_minute_bars(freq="m5")`，60s 缓存，喂给 `build_snapshot`；
- **14:50 预计算**：`_v3_precompute_positions()` 在 tail_preview 调 agent 并落 `data/wolf_v3_agent_cache.json`；14:55 `ask_agent_cached()` 直接读；
- **PG 幂等**：`wolf_v3_actions` 唯一键，`pg_action_exists/pg_record_action`；JSONL 仍兜底；
- **PG 事件**：`stop_v3_events` 表，`_v3_log()` 双写（`WOLF_V3_PG_LOG=1`）；
- **单日/单票上限**：`WOLF_V3_MAX_DAILY_SELLS=3`、`WOLF_V3_MAX_SYMBOL_PCT=0.60`；
- **live**：`.env` 已设 `WOLF_V3_LIVE=1`，worker 已重启。

已验证：

- 30 个相关测试通过；
- PG `stop_v3_events` / `wolf_v3_actions` 建表并实测插入；
- dsh 容器 `/chat` 实测返回合法决策；
- worker 启动无 v3 异常；
- 当前 `paper_positions` 无持仓，所以上线后不会立刻产生卖单。

尚未做：

- 3×15 的 **45 分钟事件检测** 仍不是独立事件源，统一在 14:30 问一次；
- 放量即时仍依赖 `WL.evaluate()` 触发；
- Case/ranker 第一档仍未做（用户明确先不做）；
- 14:50 预计算是串行，最多 8 只，可能耗时数分钟。

---

## 8. 反抽卖出 watcher（2026-10-24 补）

- PG 表 `wolf_v3_rebound_watch`：`event_date/symbol/zone_low/zone_high/break_price/target_pct/status`；
- agent schema 新增 `rebound_zone`；`action_mode=rebound_exit` 时必须给；
- `_v3_agent_positions()` 收到 rebound_zone 后落表，不当场卖；
- `_v3_check_rebound_watches()` 每 30s 轮询：
  - 现价进入 zone 且不在 13:00-14:30 → 卖（默认 `WOLF_V3_REBOUND_TARGET=0.5`）；
  - 13:00-14:30 → 标 `pending`，14:30 后仍在带内再卖；
  - 14:55 未触发 → `expired`；
- 同一 `(date,symbol,zone_high)` 通过 `wolf_v3_actions` 幂等。

注意：A 的 09:45 反抽只有在 09:45 前已经创建 watch 才会触发；当前 watch 主要由 14:30/14:55 的 agent 创建，**早盘反抽仍可能漏**。要覆盖早盘，需要再加 `09:45/10:30` 的 agent 扫描窗口。

---

## 9. 3×15 / 放量改为 30s 轮询

- `wolf_v3_intraday.py`：
  - `completed_15m()`：只聚合已完成 15min；
  - `detect_3x15()`：跌破后连续 3 根已完成 15min 收在下方 → confirmed；
  - `detect_volume_break()`：最近一根已完成 5min 收盘跌破 + 量比 ≥ `WOLF_V3_VOL_RATIO`（默认 1.5）；
- `wolf_v3_intraday_watch` PG 表：`date/symbol/level/mode/break_time/bar_count/last_bar_time/volume_ratio/status`；
- `_check_intraday_watches()` 每 30s：
  - 从快照取 broken 位，最近 3 档；
  - 跑 3×15 / 放量检测；
  - 确认 → 落 watch；13:00-14:30 标 `pending`，否则调 agent 判定；
  - 反抽型 → 转 `rebound_zone`；
  - 非反抽 → `plan_sell` + `execute_plan`；
- 去重：`_v3_intraday_done` + `wolf_v3_actions`。

生产状态：`WOLF_V3_LIVE=1`，worker healthy，35 个相关测试通过。

注意：13:15 的放量事件在禁区，会标 pending；14:30 的 agent 扫描再决定是否执行。

---

## 10. 反抽 vs 破位拉回：invalidation（2026-10-24）

- `wolf_v3_rebound_watch` 新增 `invalidation_level`（默认 = `zone_high`）。
- `_v3_check_rebound_watches()` 每 30s：
  - 现价进入 zone → 反抽卖；
  - 现价 > invalidation_level → **结构修复/假破，标 `invalidated`，不卖**；
  - 13:00-14:30 → `pending`；
  - 14:55 → `expired`。
- 确认前拉回：`detect_3x15` 在 3 根内收回会重置计数，不产生 confirmed。
- 确认后拉回：`rebound_watch` 的 invalidation 取消反抽卖。

注意：当前 invalidation 用 **现价** 判断，盘中一根插针冲过 zone 上沿也会取消；严格版应改成“收盘/15min 收盘 > invalidation 才取消”。
