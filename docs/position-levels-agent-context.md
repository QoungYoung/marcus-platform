# 持仓点位（agent 算出的 down/up）注入交易 agent

> 来源：`position_levels_daily`（19:10）→ `jobs/update_position_levels.py` → v3 agent 算 down/up → PG `position_levels`。
> 注入：`backend/app/services/trade_graph.py::_read_position_levels_context` → state `position_levels_context` → Pi prompt。

## 1. 数据是什么

每只持仓一条：

| 字段 | 含义 |
|---|---|
| `down_trigger` | 跌破位（agent 的 break_trigger）：收盘跌破 → 止损/减仓参考 |
| `up_trigger` | 反抽/反弹上沿（rebound_zone 上沿）：到附近且放量滞涨 → 减半/高抛参考 |
| `status` | agent 的状态文字（已跌破/支撑中/上方压力） |
| `updated_at` | 生成时间；超过 3 天会在 prompt 里标 ⚠️ |

## 2. Prompt 里的样子

```
## 持仓点位（agent 算出的 down/up，供参考，不替代纪律）
- SH600000 持1000股 成本98.00 现价105.00 ｜down=100.00 ｜up=120.00（距down +5.0%）（距up -12.5%）｜holding ｜更新10-30 19:10
- 用法：跌破 down 且收盘确认 → 止损/减仓；到 up 附近且放量滞涨 → 减半/高抛；未到不动。
- 这些是参考位，不替代浪型/主线/纪律门；勿把 down/up 当 guaranteed 目标。
```

## 3. 给 agent 的使用规则

`prompt_seeds.py` 第七节已加：

- **跌破 down 且收盘确认** → 止损/减仓；盘中刚破不算（2026-01-29「收盘跌破我才出」）。
- **到 up 附近且放量滞涨** → 减半/高抛；未到不动。
- down/up 只是参考位，不替代浪型/主线/纪律门；点位超 3 天未更新要警惕。

## 4. 覆盖范围

- `auto_trade`（Pi / trade_graph）：✅ 已注入 `position_levels_context`。
- `stock_agent_position` / `theme_sleeve_trade`：**不走 trade_graph**，不会自动看到；如果也要参考，需要在这两个 job 里单独读 `position_levels_store.load()` 并写进 calc_position 前的事实底稿。
- `sell_gate` / `pre_trade_gate`：目前不把 down/up 当硬门（它们是 agent 算的参考位，不是狼大原话）；仍按 MA5/34/60/B8 判。

## 5. 测试

`backend/tests/test_position_levels_context.py`：

- 有 levels：输出 down/up、现价、距 down/up、用法；
- 无 levels：输出“未生成，以 portfolio 成本/既有止损线为准”。
