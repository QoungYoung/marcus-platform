## Why

AI 主导做T已上线（ai-led-t-trading），但实测（任务 #17/#18）暴露决策质量瓶颈：AI 已学会"少做"（#18 中 28 次触发放弃 18 次、避免追高），但**成交的 5 笔胜率 0%**——AI 不知道"这只票低吸后到底涨不涨"，每次唤醒都是无历史记忆的新交易员；回测/实盘也缺乏 exec 胜率、abandon 正确率等决策质量指标，无法定位是"过度保守"还是"判断不准"。

## What Changes

- **决策反馈闭环**（核心）：AI 决策成交后回填实际结果——成交价、后续 N 根 bar 走向、实际盈亏，写入 `t_ai_actions.outcome`；下次唤醒同一标的时，AI 获得"最近 5 次决策 + 结果"（如"你上次 64.79 放弃是对的，之后跌了 2%；62.57 exec 后跌了 1.5%，该价位支撑不牢"），让 AI 基于真实行为模式迭代判断。
- **决策质量指标**：回测与实盘统计 exec 胜率（放行的单子赚没赚）、abandon 后行情验证（放弃后涨=错杀、跌=正确）、wait 转 exec 比例，输出到报告与 `/t/ai/actions`。
- **决策上下文增强**：唤醒消息附带该标的做T历史统计（近 N 次低吸触发后 30 分钟平均走向、exec 胜率、连续命中行为），并强化决策 checklist（价差盈亏比/底仓弹药/历史模式/连续命中）。
- **回测对齐**：回测引擎同样回填 outcome（成交后看后续 bar 走向），使回测能验证"反馈闭环是否提升决策质量"。

## Capabilities

### New Capabilities

- `t-ai-decision-feedback`: AI 做T决策的反馈闭环与质量度量——决策结果回填（outcome：成交价/后续走向/实际盈亏）、决策质量指标（exec 胜率/abandon 正确率/wait 转化）、带历史统计的决策上下文（最近决策+结果、标的做T行为统计、决策 checklist）。

### Modified Capabilities

- `t-ai-agentic`: 「AI 决策主体与职责边界」与「决策审计」需求扩展——审计记录从"决策+网关结果"扩展为"决策+网关结果+成交后结果(outcome)"；AI 决策输入从"当前快照+最近决策"扩展为"当前快照+最近决策及结果+标的做T历史统计"。

## Impact

- **后端**：`backend/app/services/t_ai_agent.py`（outcome 回填逻辑、决策质量统计、上下文组装）；`backend/app/services/t_db.py`（t_ai_actions outcome 列读写、质量指标查询）；`backend/app/services/t_bridge.py`（唤醒上下文增强：带结果的最近决策 + 标的统计）；`backend/app/api/t_account.py`（/t/ai/actions 返回质量指标）。
- **回测**：`backend/app/services/t_backtest.py` / `t_backtest_runner.py`（回放中成交后回填 outcome、决策质量指标入 metrics）。
- **桥接**：`docker/dsh/bridge/lib/index.js`（决策 checklist 强化 prompt、上下文格式）。
- **前端**：TBacktestPage AI 决策面板/报告展示 exec 胜率与 abandon 正确率（可选）。
- **数据**：PostgreSQL `t_ai_actions` 增 `outcome` JSONB 列（幂等迁移）。
