## 1. 移除所有自动建仓来源的人工确认（D1）

- [x] 1.1 `t_build.py::validate_build_position`：第 7 步人工升级分流改为——自动来源（agent/daily_auto/ai_led）遇 `mode == "human_confirm"` 时放行为 `mode="normal"`，`up_reason` 追加进 `warn`（不再返回 human_confirm）
- [x] 1.2 `t_build.py::build_gateway_execute`：删除「置事件 status='human_confirm' 并返回 human_confirm」的分支（D1 后该分支不可达）
- [x] 1.3 确认 `classify_build_escalation` 函数体不变（B1-B8 分类保留供告警与回测同源），HALT（B3）→ blocked 路径不变
- [x] 1.4 确认人工确认端点（`POST /t/build/events/{id}/confirm`）保留但仅处理遗留事件，不产生新 human_confirm 事件

## 2. 当日配额口径（D2）

- [x] 2.1 `t_db.py::count_today_builds`：统计口径改为 `status = 'executed'`（总笔数与单票分支一致），更新 docstring
- [x] 2.2 核对 `t_build.py::classify_build_escalation` B8 分支对 count 口径的引用（仍成立：已达上限时作为告警而非升级）

## 3. 规模档放开（D3）

- [x] 3.1 `t_build.py::build_sizing`：`mode == "mom_etf"` 时不追加「总底仓超上限」reason（跳过 total_floor_max 校验），保留单笔 30%/单标 30% 与 100 股保底
- [x] 3.2 确认 `build_t_position` 的 `amount > single_max_amount` 拒绝分支对 mom_etf 仍生效（未改动）

## 4. 持仓识别与双周节律（D4/D5）

- [x] 4.1 `t_mom_etf.py::_mom_positions`：改为基于 `get_sellable_ledger()` 实际可卖持仓（sellable>0），`avg_price/built_at` 优先回填最近成交事件，否则用账本均价
- [x] 4.2 `t_mom_etf.py::_last_rebalance_date`：改为读 `t_build_scan_results`（source='mom_etf' AND status='executed'）的最大 trade_date
- [x] 4.3 `t_mom_etf.py::try_rebalance`：本次调仓产生任一成交（success/filled/executed）后，将当日 source='mom_etf' 候选置 status='executed'（消费信号）；全部未成交则保持 pending 供次日重试
- [x] 4.4 `t_mom_etf.py::scan_once`：确认清理逻辑不误删已 executed 的历史候选（仅清理当日 pending/当日全量重写，保留 executed 记录）

## 5. 观测性（D6）

- [x] 5.1 `t_mom_etf.py::try_rebalance`：每条买/卖结果 `logger.info` 记录 symbol/action/status/reason；`no_price` 或全部无成交时 `logger.warning`

## 6. 测试

- [x] 6.1 `backend/tests`：新增用例——自动来源（ai_led）遇 regime=CAUTIOUS 不产生 human_confirm 事件、直接放行（monkeypatch regime）
- [x] 6.2 新增用例——首开新标的/单笔超标准档等 B 分类不再升级（validate 返回 normal 且 warn 含风险原因）
- [x] 6.3 新增用例——HALT 下建仓仍被拒（blocked 路径未放宽）
- [x] 6.4 新增用例——未成交（human_confirm/pending）事件不占当日建仓配额（count_today_builds 只计 executed）
- [x] 6.5 新增用例——mom_etf 档总底仓超 60% 时 sizing 仍通过（单笔/单标上限仍校验）
- [x] 6.6 新增用例——`_mom_positions` 识别非 mom_etf 来源持仓（如 daily_auto 建的 SH515880）且不重复买入
- [x] 6.7 新增用例——调仓成交后 scan_results 候选置 executed 且 `_last_rebalance_date` 生效（节律恢复）
- [x] 6.8 回归：`python -m pytest backend/tests -q`（现有 141 用例 + 新增）

## 7. 部署与验证

- [ ] 7.1 提交到 main（中文 commit message），确认 prod `/opt/marcus-platform` 与仓库无未提交漂移（`git diff` 核对 t_mom_etf.py 等）
- [ ] 7.2 prod 拉取 → 重建 backend/worker 镜像 → `docker compose up -d` 重启
- [ ] 7.3 验证 `/api/v1/t/mom-etf/status` 正常；下一建仓窗口观察：候选从「待处理」变「已执行」、t_build_events 出现 executed 建仓、无新 human_confirm 事件、日志逐条记录结果
