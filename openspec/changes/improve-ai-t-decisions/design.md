## Context

现状（见 proposal.md - Why）：AI 主导做T已上线，AI 会保守放弃追高（#18：28 触发放弃 18），但成交胜率 0%——无决策反馈闭环，AI 每次唤醒无历史记忆；无决策质量指标无法定位问题。`t_ai_actions` 已有 input_snapshot/output/gateway_result，`_recent_decisions` 只给最近决策不带结果。

## Goals / Non-Goals

**Goals:**
- 建立「决策 → 成交 → 结果回填 → 下次决策参考」的反馈闭环，AI 基于真实行为模式迭代
- 输出决策质量指标（exec 胜率/abandon 正确率/wait 转化/平均盈亏），回测与实盘可量化对比
- 唤醒上下文带标的做T历史统计 + 决策 checklist，提升单次决策质量

**Non-Goals:**
- 不做收益目标函数/强化学习自动调参（先建数据闭环，人工/AI 复盘据此迭代）
- 不改网关风控（ai_led 档位与全链校验不变）
- 不新增 LLM 供应商或训练模型

## Decisions

### D1. outcome 数据结构与回填时机
`t_ai_actions.outcome` JSONB：
```json
{"kind": "exec", "fill_price": 62.57, "exit_price": 63.1, "bars_after": 6,
 "direction": "up", "pct_change": 0.85, "hit_target": false, "hit_stop": false,
 "realized_pnl": 120.5, "assessed_at": "2026-06-02 11:30:00"}
```
- **实盘**：exec 成交后由 worker 侧定时任务（或网关成交回调）按标的拉后续 m5 评估（成交后 30 分钟窗口），回填 outcome。
- **回测**：引擎在撮合后继续回放 N 根 bar，用后续 bar close 计算 outcome（防前视：只用成交后 bar），任务完成落库时一并写。
- **wait/abandon**：回填 `kind: wait/abandon + verify`（放弃后 N 根 bar 走向，供正确率统计），非强制。
- **备选**：成交价+实时价即时报。否决——做T盈亏需"后续走向"而非瞬时价，30 分钟窗口更符合回转语义。

### D2. 决策质量指标计算
`t_ai_agent.py` 新增 `decision_quality(symbol=None, trade_date=None)`：
- exec 胜率 = outcome.pct_change>0 的 exec 数 / exec 总数（低吸看后续涨、高抛看后续跌 → 用方向归一：买涨卖跌）
- abandon 正确率 = 放弃后行情向"不该做"方向（低吸后续继续跌/高抛后续继续涨）占比
- wait 转化 = wait 后同条件再次触发且转 exec 比例
- 分布：exec/wait/abandon 计数 + 平均 pct_change
- 回测：引擎汇总入 metrics（`exec_win_rate_pct/abandon_correct_rate_pct/ai_wait_to_exec_rate_pct`）；实盘：`/t/ai/actions` 响应带 `quality` 聚合。

### D3. 唤醒上下文增强（t_bridge.wake_agent）
`_recent_decisions` 改为返回"决策+outcome 摘要"（最近 5 条，含结果）；新增 `_symbol_t_stats(symbol)`：
- 该标的近 20 次低吸触发后 30 分钟走向（平均 pct_change、胜率、达目标价率）
- exec 历史胜率、abandon 正确率
拼入唤醒消息的"历史模式"段 + 决策 checklist（价差盈亏比/弹药/历史胜率/连续命中），T_BUILD_SYSTEM_PROMPT 同步更新。

### D4. 回测 outcome 回填
`TBacktestEngine`：撮合后记录成交 (symbol, price, bar_idx)，后续每根 bar 更新"成交后第 N 根"；收盘或任务结束把 outcome（后续 6 根 bar 走向、按 low_buy 买看涨/高抛卖看跌归一）写入 result 的 ai_decisions outcome。`run_task` 落库时合并写入 t_ai_actions（回测决策已存审计，回填 outcome 字段）。
- **防前视**：outcome 只统计成交 bar 之后的 bar，不参与任何决策计算。

### D5. 前端展示（可选但推荐）
TBacktestPage 报告区：metrics 卡片加 exec 胜率/abandon 正确率；AI 决策面板每条显示 outcome 摘要（✅+0.85% / ⛔-1.5%）。

## Risks / Trade-offs

- [outcome 回填依赖后续行情可用] → 回测保证（缓存内后续 bar 必有）；实盘若 m5 拉取失败则 outcome 留空（不影响统计，标记 assessed_at 空）。
- [反馈可能强化错误模式（过拟合短期）] → 用 30 分钟窗口 + 胜率阈值展示，不做自动参数调整；人工/AI 复盘把关。
- [指标口径（低吸买涨/高抛卖跌归一）复杂] → 统一"方向归一"函数（side 已知），metrics 明示口径。
- [回测耗时增加（需回放成交后 bar）] → 成交后 bar 本就在回放窗口内（无额外拉取），仅增加统计计算，开销可忽略。

## Migration Plan

1. `database.py` `_apply_ai_led_migration` 增 `t_ai_actions` 加 `outcome JSONB` 列（`ADD COLUMN IF NOT EXISTS`，幂等）。
2. `t_db.py`：insert_ai_action 支持 outcome；新增 `update_ai_action_outcome(id, outcome)`、`list_ai_actions` 返回 outcome。
3. `t_ai_agent.py`：`_record_outcome()` 回填逻辑 + `decision_quality()` 统计。
4. `t_bridge.py`：`_recent_decisions` 带结果 + `_symbol_t_stats()` + 唤醒消息 checklist；prompt_seeds.py T_BUILD 更新。
5. `t_backtest.py`：引擎 outcome 计算；`t_backtest_runner.py` 落库回填。
6. `t_account.py`：`/t/ai/actions` 带 quality 聚合。
7. 前端（可选）：指标卡片 + 决策面板 outcome。
8. 测试：outcome 回填（实盘 mock/回测真值）、质量指标计算、唤醒上下文含历史结果；回归 t 套件。
9. 部署：backend/worker + dsh（bridge 变更 docker cp 进卷）+ FORCE_RESEED_PROMPTS；跑 1 个月回测对比反馈前后 exec 胜率。

## Open Questions

- outcome 评估窗口（30 分钟 = 6 根 m5）初定，后续可按做T周期调（无需改 spec）。
- 实盘 outcome 回填的触发时机：初定由 `ai_daily_review`（收盘）统一回填当日成交，避免盘中加线程；如需盘中即时反馈再增调度。
