## Context

见 proposal.md——Why。当前 t 账户建仓校验链（`t_build.py`）存在人工确认升级：`classify_build_escalation` 对 regime=CAUTIOUS（B4）、首开新标的（B1）、单笔超标准档（B2）、连续亏损期（B5）、日亏预警（B7）、近跌停（B6）、当日触犯风控（B8）返回 `human_confirm`，随后 `validate_build_position` 第 7 步与 `build_gateway_execute` 对自动来源（agent/daily_auto/ai_led）把事件置为 `human_confirm` 并暂停等待人工。08-25 实测：2 个事件卡死后 `count_today_builds`（只排除 rejected/cancelled）恒 ≥3，撞 `max_daily_auto=3`，mom_etf 当日 190 次尝试全被拒。另有 `build_sizing` 短线档「总底仓 ≤ 净值×60%」上限在账户已持 53% 底仓时锁死建仓；`t_mom_etf.py` 持仓识别/节律基准依赖会被覆盖的 reason 字段而失效。生产卡死事件 301/302 已人工置 cancelled。

约束：`t_build.py`/`t_db.py` 的护栏为 t 账户全部建仓来源共享；prod 为 docker compose 部署，改动需重新构建镜像重启。

## Goals / Non-Goals

**Goals:**
- t 账户所有自动建仓来源（agent/daily_auto/ai_led）不再因 B1/B2/B4/B5/B6/B7/B8 升级人工确认，一律自动放行；风险分类以告警保留。
- 当日建仓配额只计已成交（executed），未成交事件不消耗配额。
- mom_etf 调仓不受「总底仓 ≤60%」约束（单笔/单标上限保留）。
- mom_etf 已持有识别基于 t 账户实际可卖账本；双周节律以 mom_etf 实际成交记录为基准；调仓结果逐条落日志。

**Non-Goals:**
- 不改动 HALT（B3）强制拦截——一切建仓（含人工）仍被拒。
- 不删除人工确认 API 端点（遗留 human_confirm 事件仍可处理），只是不再产生新事件。
- 不做 schema 迁移（不新增列/表）。
- 不改变贪婪门控、动量信号、扫描逻辑本身；不改动其他短线档（trend_break/vrebounce/vreb_etf）的总仓上限。

## Decisions

### D1: 移除所有自动建仓来源的人工确认（消费点短路）

在 `validate_build_position`（`t_build.py`）第 7 步：自动来源（agent/daily_auto/ai_led）遇到 `mode == "human_confirm"` 时不再返回 human_confirm，改为 `mode="normal"` 放行并把 `up_reason` 追加进 `warn`（随结果与日志输出）；`build_gateway_execute` 的 human_confirm 分支（置事件 status='human_confirm' 并返回）随之不再被触发，删除该分支。
- 理由：用户明确要求「去掉所有人工确认」——无人值守策略在无人工值班时，升级只会卡死配额（08-25 实测 190 次重试被拒）。
- 保留：`classify_build_escalation` 函数体不改（B 分类逻辑与回测同源），仅在消费点把 human_confirm 视为放行+告警，最小侵入；HALT（B3）返回的 `blocked` 路径不变，仍强制拦截。
- 保留：人工确认端点（`POST /t/build/events/{id}/confirm`）用于处理历史遗留 human_confirm 事件，不再产生新事件（REQ-GATE-003）。

### D2: 当日建仓配额只计 executed

`t_db.count_today_builds()`（含单票分支）统计口径从 `status NOT IN ('rejected','cancelled')` 改为 `status = 'executed'`。
- 理由：审计先行插入的 `pending_confirmation` 与升级后的 `human_confirm` 均非实际建仓，计数它们会自计/毒化配额。
- 影响：所有建仓来源的当日配额/单票限制口径统一为「实际成交」；人工确认 execute 放行不受影响（放行后事件变 executed 才计数）。**BREAKING** 但语义更正确（REQ-GATE-004/005）。

### D3: mom_etf 档跳过总底仓上限

`build_sizing` 中 `mode == "mom_etf"` 时不再追加「总底仓超上限」reason（等价 total_max 不参与校验），保留 `single_max`（30%）与 `per_symbol_max`（30%）约束与 100 股保底逻辑。
- 理由：mom_etf 目标组合 ≤3 只、单只 ≤30%，即使账户已持其他策略底仓，总名义仍 ≤90%——60% 总仓上限与动量轮动的换仓目标冲突（会永久锁死建仓，见 08-25 实测）。
- 备选：全部短线档统一放开——影响 V反/趋势突破，超出用户范围，放弃（REQ-MOM-010）。

### D4: 持仓识别基于实际账本

`_mom_positions()` 改为直接取 `get_sellable_ledger()` 中 `sellable > 0` 的全部 t 账户持仓；`avg_price/built_at` 优先从最近成交事件回填，否则用账本均价。
- 理由：t 账户是共享账户，SH515880 由 daily_auto 建仓后 mom_etf 无法通过 reason 识别 → 重复买入且永不换出。基于账本后：已持有标的跳过买入、参与换出判断（REQ-MOM-013）。

### D5: 双周节律以 mom_etf 实际成交记录为基准

`_last_rebalance_date()` 改为读 `t_build_scan_results`：`source='mom_etf' AND status='executed'` 的最大 `trade_date`；`try_rebalance` 在本次调仓产生任一成交（status in success/filled/executed）后，将当日 `source='mom_etf'` 候选置 `status='executed'`（消费信号，页面从「待处理」变「已执行」）。
- 理由：`t_build_events.reason` 会被审计更新覆盖，无法作为稳定标记；scan_results 是 mom_etf 自有表，不受其他策略影响，且顺带解决「候选永远 pending」的展示问题。
- 备选：按 events 表 `event_type+status=executed` 统计——会把其他策略成交计入节律（V反/daily_auto 频繁成交 → mom_etf 节律被无限推迟），放弃。
- 语义：全部被拒（无成交）→ 候选保持 pending、节律不更新 → 次日窗口继续重试，符合 REQ-MOM-010「被拦截次日重试」。

### D6: 调仓结果逐条落日志

`try_rebalance` 每条买/卖结果 `logger.info` 记录（含 `no_price`、护栏拒绝 reason），无成交或全部 `no_price` 时 `logger.warning`。
- 理由：08-25 只有「结果 3 项」计数，失败原因完全不可见。

## Risks / Trade-offs

- [人工确认闸门移除] 所有自动建仓即时成交，无人审阅 B1-B8 风险 → 用户明确决策；风险原因以告警（warn/日志）保留可审计；HALT 熔断仍拦截。若日后需要恢复，可在消费点重新启用（本次不提供开关）。
- [D2 影响其他来源] count_today_builds 改 executed-only 后，V反/趋势突破的当日配额语义同步变化 → 回归 `backend/tests` 全量 + 手工验证一次自动建仓。
- [D5 候选状态语义] scan_results 从 pending → executed 表示「已消费」，前端列表按 status 排序（pending 优先）会随之变化 → 前端无需改（executed 正常展示），确认页面过滤逻辑即可。
- [prod 与 repo 漂移] prod `/opt/marcus-platform` 的 t_mom_etf.py 与仓库有未提交差异（`_last_rebalance_dt` 变量名等）→ 部署前 `git diff` 确认，以仓库版本为准合入。

## Migration Plan

1. 提交代码到 main（含测试）。
2. prod：`git pull` → 重新构建 docker 镜像（backend/worker）→ `docker compose up -d` 重启 worker+backend。
3. 验证：`/api/v1/t/mom-etf/status` 正常；下一自动建仓窗口（9:45-13:00）观察候选是否被消费（scan_results 变 executed）与 t_build_events 是否出现 executed 建仓；确认不再产生 human_confirm 事件。
4. 回滚：`git revert` 本次提交 → 重建镜像重启；数据层面无迁移，无需回滚脚本。
