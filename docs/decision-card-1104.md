# 决策卡重写（2026-11-10）：defense 允许 probe ≤10% + 强制五段卡

> 变更范围：交易 agent 的规则（提示词 + 仓位档位 + 结果落库），**不动** calc_position 公式、
> 不放开 new_base/add_base、不放开 exit/G2/G3。目标：把「资格否决」改成「预算内下注」。

## 一、改了什么

| 层 | 之前 | 现在 |
|---|---|---|
| 浪型 defense | 「不新开底仓，只做T/减仓/观察」 | **允许 probe 试探仓 ≤10%**；new_base/add_base 仍为 0 |
| exit / G2 / G3 | 禁止 | **不变**（只做T/减仓/观察） |
| probe 条件 | — | ① 结构证据 ≥2 项（b_rebound 三条件 / two_jump 二段跳 / chip_cost 上方筹码）② 决策卡五段齐全 ③ 先 check_entry_filters 再 calc_position |
| 输出 | 结论 + 证据 + SIGNAL | 追加 **DECISION 五段卡**（action/target/trigger/deadline/size/invalid），哪怕不做 |
| 落库 | 无 | `trade_decision_card`（PG），缺卡会在报告里标 ⚠️ |

改动位置：
- `config/p3_position_tiers.json`：defense.intents 增加 `probe`（cap 10）
- `backend/app/services/trade_graph.py`：§1 规则、§7 仓位、§13 决策卡；`_parse_decision_card` / `_save_decision_card`
- PG `prompts` 表 `T_TRADE_SYSTEM_PROMPT` v6 → v7（旧版存档 `docs/prompt-archive/T_TRADE_SYSTEM_PROMPT_v6.md`）

## 二、11/04 回放：新规则会写出什么卡

当时的四类证据（全部 as-of 11/04，验收报告 `docs/acceptance-1104-inputs.md`）：

```text
浪型：down / 下跌1浪 / defense
B反：3/3（双脚 ✅ 缩转放 ✅ 5日线 ✅）；13:45 turn=3.34×；仓位上限 30%
二段跳：13:00 半导体首新高（rank 1），存储 13:32、CPO 13:38、PCB 13:46
筹码：半导体材料/设备上方筹码 99%
子链：设备 +14.92%（25/25）、材料 +12.00%（31/31）、PCB +6.84%（落后）
```

新规则下，13:45 应写出的卡：

```text
DECISION:
- action: probe
- target: 半导体（512480 / 半导体材料·设备）
- trigger: b_rebound 3/3 且 two_jump=半导体（13:00 首新高）
- deadline: 14:30
- size: 10%
- invalid: 上证跌破 3590.80（11/04 低点）或 b_rebound 掉回 2/3
```

**和狼大的差别要说清楚：他 13:59 是半导体 5%→30%，我们只给 10%。**
这是有意的保守：10% 是「试探仓」预算，不是「反弹仓」上限；要把上限抬到 30%，必须先有 L3（站稳 3674 三天）或人工确认。
11/04 当天数三没成立（他 11/06 自己也说「还在筑双脚」），所以 30% 属于事后看对的加仓，不是规则内可复制的动作。

旧规则下的输出是：`action=none`，理由「浪型 defense + 主线 weak」——就是我们 11/04 14:20 实际推送的那份。

## 三、怎么验证

1. **今天 14:20** 的 `auto_trade_afternoon` 跑完后，查：

```sql
select trade_date, trade_window, action, target, trigger_text, deadline, size_pct, invalid_text
from trade_decision_card order by id desc limit 3;
```

2. 如果没有卡：报告里会出现「⚠️ 决策卡缺失」——说明模型没按格式输出，不是代码没生效。
3. 仓位上限自检：`three_tier_gate(wave_state={'operation':'defense'}, intent='probe')` → `allowed=True, cap=10`；
   `intent='new_base'` → `allowed=False, cap=0`（已测）。

## 四、回滚

- 代码/config：`git revert` 对应提交；
- 提示词：用归档的 v6 覆盖 PG（`update prompts set content=...`），或把 `FORCE_RESEED_PROMPTS` 设为 true 重启 backend 重新种子化。
  **注意**：默认 `seed_prompts` 只插入不覆盖，所以 PG 里 v7 是权威；只有显式 FORCE_RESEED 才会被种子覆盖。
