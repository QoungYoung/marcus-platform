## 1. P0 前置探针（已完成，验证性）

- [x] 1.1 数据源客户端 `backend/scripts/p0_probe/data_sources.py`（腾讯 qt / 腾讯 ifzq mkline / 新浪 minline / brze tushare 代理）
- [x] 1.2 探针① 腾讯 qt 30s×20只成功率/延迟/风控 + 指数实时（probe1_tencent_qt.py）→ 100% 达标
- [x] 1.3 探针② 可T质量三代理好Tvs差T分隔性（probe2_t_quality.py）→ 价差空间强分隔/O-C 中分隔/往返度弱(改1min)
- [x] 1.4 探针③ 一交易日日内波动/量能分钟分布（probe3_intraday_profile.py）→ 早盘主峰/U型量能
- [x] 1.5 探针④ 分钟线可用性 + 双源一致性（probe4_minute_availability.py / probe4b_dual_source.py）→ 三源 100%、价差<0.1%
- [x] 1.6 探针⑤⑦⑧ brze 串行/延迟/一致性验证（probe5_brze_serial.py / probe6_latency_compare.py / probe7_brze_latency.py / probe8_brze_consistency.py / probe8b_brze_consistency.py）→ 与腾讯 1min 价差 0.0000%
- [x] 1.7 P0 报告 `p0_report.md`（数据源总表 + 探针结论 + 落地修正）

## 2. P1 选股层（t_account 账户 + 三层池 + 可T质量打分）

- [x] 2.1 数据库迁移 `_apply_t_account_migration()`：注册 account_id='t' 进 paper_accounts（独立 initial_capital）+ 建 t_conditions/t_triggers/t_regime_state/t_daily_state/t_risk_state 五张表（幂等，对齐 `_apply_paper_account_migration` 范式）
- [x] 2.2 分钟线客户端沉淀为服务层模块（腾讯 ifzq mkline 主 / 新浪备 / brze 权威校验；brze 按卖家约束单线程串行+间隔≥1s+失败重试、实时分钟 freq 大写），供选股/量比/企稳/regime 复用
- [x] 2.3 可T质量打分函数：价差空间（振幅中位−2×(滑点+手续费)>0 硬门槛）、O-C 回归度（≤0.45 加分/≥0.55 减分）、日内往返度（1min 粒度重算）、流动性（log成交额/换手适中/5分钟均匀度）、风险惩罚（隔夜跳空/涨跌停概率/连板情绪）、成本占比
- [x] 2.4 三层池数据结构与流转逻辑：底仓候选池（打分建仓，不做T）→ 做T实盘池（已持仓+可T达标+过regime门+底仓≥下限，唯一可触发）→ 观察池（缓冲）；禁止无底仓标的生成做T条件
- [x] 2.5 选股 Agent（DSH）能力：从实盘池生成 t_conditions 条件元组（触发价/复归价/量比阈值/企稳确认/卖出目标/止损/时间止损/regime_gate），仅 account_id='t' 且有底仓标的
- [x] 2.6 t 账户执行器：`MarcusVNPyExecutor(account_id='t')` 独立实例与账本访问（复用现有多账户体系）

## 3. P2 监控触发（TMonitor + 表 + 状态机 + 量比 + regime 闸门）

- [x] 3.1 TMonitor 注册进 worker_main（30s 周期 + 初始偏移 `time.sleep(20)` 错峰 + `_is_trading_time` 门控 + worker_status/worker_commands 控制）；分层采样：核心底仓(≤10-20)腾讯 qt `use_cache=False` 直连 + `ThreadPoolExecutor(≤5)` 并发 + jitter，观察池 30s-1min 缓存
- [x] 3.2 盘中量比归一：修正 `indicator.py:2224` `turnover_rate/2.0` → `[当前累计换手×(240/已开连续分钟)]/近N日同刻均值`；`benchmark_turnover_profile` 用腾讯 m5 6日/新浪 300 根/brze stk_mins 构造（无需自积累）
- [x] 3.3 滞回/去抖/armed 状态机：触发价与复归价两档、cooldown、armed_at/last_triggered_at/trigger_count_today、价源一致性
- [x] 3.4 复合企稳确认：价格到位（支撑位）∧ 量能企稳（量比归一）∧ 分时企稳（1min/5min 不再创新低/下影线/量能萎缩回升）∧ 波动结构允许
- [x] 3.5 t_regime_state 三层合成闸门：L1 日频基准（复用 `market_diagnosis` state/score_* + 指数日线 MA20/60）、L2 日内动态前哨（腾讯 qt 指数实时跌幅/破5日线，分钟级）、L3 硬保险丝（沪深300跌>2%→无条件 HALT）；三态 ACTIVE/CAUTIOUS/HALT + 量能解读符号；TMonitor 写 t_triggers 前先过 GATE（BLOCKED 不写 / MANUAL_ONLY 挂人）
- [x] 3.6 t_conditions/t_triggers 表落地 + t_triggers 状态机：`pending → (auto_ready|human_confirm) → executed|blocked|cancelled`；snapshot JSONB 分层（quote_time/trigger_price/quote_price/suggest_bid/ask/slippage_budget/confidence）；`UPDATE...WHERE status='pending' RETURNING` 原子消费；human_confirm 超时自动 cancelled
- [x] 3.7 触发事件生成：命中→写 t_triggers(pending) 快照；时段因子（early 1.0/mid 0.5/late 0.3）加权；14:45 后禁新开仓

## 4. P3 桥接唤醒（Worker→bridge /chat→Agent→网关）

- [x] 4.1 Worker 主动唤醒：TMonitor 命中后 POST bridge `/chat` 携带触发上下文唤醒做T Agent；桥不可达降级 30s 低频轮询兜底；Worker 永不直接调用下单接口
- [x] 4.2 做T Agent 复核决策：读 t_triggers 快照 + 合理性判断（默认自动、异常升级）；不轮询
- [x] 4.3 网关包装层：`place_order` 统一包装（三阶校验：硬闸门[裸空/跌停/STOP_ALL/白名单 O(1)快路径]→账本[可卖底仓断言/买腿≤可卖底仓/日亏回转额熔断]→建议层[单笔%/冷却/价差成本比/频次护栏仅告警]）；account_id 白名单隔离不影响 stock 主账户
- [x] 4.4 网关二段实时断言：落单前重拉最新持仓/价格/跌停/熔断（不只吃快照），过才撮合置 executed，失败置 blocked+reason
- [x] 4.5 当日可卖额度原子账本：卖腿下单原子扣减（UPDATE...WHERE 可卖≥下单量 RETURNING）、买腿成交回补、卖出在途锁定禁买腿（半边腿未落定不启动另一半）、先卖后买资金流约束
- [x] 4.6 可卖底仓分档 L0-L3（L0 禁低吸/L1 0.5×/L2 1.0×/L3 1.0-1.5×+日回转额上限）+ 净回转头寸账本（次数不设硬顶）
- [x] 4.7 异常升级 6 类清单（软件异常/歧义/首开非底仓标的/regime极端/连续触风控/孤儿单）→ human_confirm 分支，由网关状态机判定
- [x] 4.8 熔断与 STOP_ALL：t_daily_state/t_risk_state 两表、日亏损熔断、连续亏损计数、STOP_ALL 总开关仲裁一切 t 账户下单
- [x] 4.9 本地条件单：卖出端（高抛止盈/破位止损）价位触发承载秒级时效，Worker 只兜底确认
- [x] 4.10 孤儿单处置：触发→Agent→网关→撮合任一步超时未确认按撤单/重置；human_confirm 超时 cancelled

## 5. P4 闭环审计（参数标定 + 风控校验 + 审计）

- [x] 5.1 风控敏感度扫描：分档初值 ±30% 网格扫描 → 收敛窄区间 → 固化上线值（保守档起步）
- [x] 5.2 执行校验全量落地：滑点参数化假设（20-60 元双边 2-5 tick + 手续费，标注 simulated slippage estimate）+ 最低价差/成本比前置过滤（>价差空间 15-20% 不触发）+ 成交价取模拟撮合价
- [x] 5.3 高抛接回 + 踏空熔断 + 底仓保留下限：接回价≤高抛价−价差阈值/时限放弃、卖出后上行超 X% 放弃接回不追高、跌破底仓下限禁高抛转监控
- [x] 5.4 尾盘归平与跌停禁买：14:45 后禁新开仓 + 强制平当日回转头寸；跌停附近禁买 + 涨跌停/一字板委托超时判定
- [x] 5.5 全量审计日志：t_triggers 状态流转 / place_order 参数 / 网关校验结果（含 blocked reason）/ 可卖额度账本变动 / 熔断事件，关联条件单 ID 形成审计链
- [x] 5.6 前端做T页面：账户状态（资金/持仓/可卖额度）、三层池视图、监控条件列表（t_conditions）、触发事件流（t_triggers 状态机）、审计日志
- [x] 5.7 集成验收：`Worker 命中 → 唤醒 Agent → 决策 → 网关校验 → 撮合`全链路打通；自动/人工分流正确；stock 主账户不受影响；账实一致、当日归平

## 6. 文档与配置

- [x] 6.1 配置项：brze token / 腾讯 ifzq / 新浪端点配置化（pydantic-settings，.env）；数据源与限流参数（并发上限/冷却/jitter）
- [x] 6.2 做T系统设计文档更新：最终方案 final-t-plan.md 的 §10.4/10.5 与实现对齐（三源方案 + brze 约束）
- [x] 6.3 测试：t 账户迁移幂等、三层池流转、量比归一（对照旧公式消除早盘误报）、状态机无重复/悬空、网关校验矩阵（硬/账本/建议层）、可卖额度账本原子性、regime 三态切换
