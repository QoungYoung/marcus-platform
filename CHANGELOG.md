# 更新日志

本文档记录 Marcus AI Trading Platform 的主要变更。


---

## [1.6.0] — 2026-09-02（做T体系落地：狼大做T信号 + 只读动态离场监控）

- **做T监控改造（t_monitor）**：只监控股票任务账户 stock + 只跑狼大T表达式（WOLF_T_FIELDS: minute.m5.t_sell / index.intraday_dd / quote.vwap_break）；T1缩转放验证无预测力暂缓；自动维护(_daily_maintain/_ai_maintain)默认关闭。
- **网关白名单**：EXEC_ALLOWED_ACCOUNTS=stock（t 账户下单一律拒绝，env T_EXEC_ALLOWED_ACCOUNTS 可配）；build_gateway_execute 同源拦截。
- **模块屏蔽**：vrebounce/vreb_etf/mom_etf/t_build 服务停用、auto_trade 5任务停用、止损/加仓/建仓/长期池监控停用（止损改为只读动态监控恢复）；旧 t 账户条件 12 条 inactive。
- **正T买点（新信号 index.intraday_dd）**：上证5min盘中回撤 dd∈[2%,3%) → 个股低吸；5股×184天验证 T+1 +3.15% hit0.75（分半稳定）。
- **黄线跌破离场（quote.vwap_break）**：现价<分时均价线(VWAP)即离场，替代 -3% 固定止损；腾讯qt average 与 brze VWAP 交叉验证差异0.014%。
- **止损监控改造**：STOP_LOSS_DYNAMIC_ONLY=1 只读动态离场距离监控（黄线距离+分时T出前高距离）；旧8条止损距离体系屏蔽；卖腿保留100底仓（防250/252连续卖底仓）。
- **数据通道**：brze stk_mins 个股+指数分钟历史全通；已拉5只个股5min各197天。
- 生产：stock账户药明康德100股@158.742，条件 249正T买/250 T出卖/252黄线卖。
## [1.5.4] — 2026-08-11（早盘 60 分钟 MA 可用性修复）

- **`backend/app/core/trading/_60min_analysis.py`**：新增 `build_partial_60min_bar`，盘中用 1 分钟实时 K 线合成当前未完成的 60 分钟 K 线；`_fetch_60min_bars_merged` / `get_60min_ma_values` 在早盘首根 60 分钟 K 线未完成时不再返回空。
- **`backend/app/api/market.py`**：`get_intraday_min(freq='60min')` 早盘（如 9:35）即可返回 MA5/10/20/30/60 与 MACD，indicators 新增 `last_bar_time` / `last_bar_partial` 标记。

## [1.5.3] — 2026-06-27（跨窗口候选池 + 监控器对齐 + 轮询错峰）

本次更新构建了跨窗口候选池机制，将前序窗口被"时机性"拒绝的标的自动入池，由独立的守护线程实时监控并在回调到位后自动建仓。同时统一了三个监控器的生命周期管理和轮询节奏。

### 📋 跨窗口候选池：等回调再入场

- **新增 `backend/app/services/candidate_pool.py`**（~300 行）：候选池核心服务
  - 7 条件入池筛选：区分"时机性拒绝"与"结构性缺陷"
  - 状态机：`waiting → ready → promoted / expired`
  - JSON 文件持久化到 `data/candidate_pool.json`
  - `format_for_pi()`：生成 Pi prompt 候选池区块
- **新增 `backend/app/services/candidate_pool_monitor.py`**（~310 行）：候选池实时监控守护线程
  - 30 秒轮询，与止损/加仓监控并行运行
  - 条件到位时自动：`check_entry_filters → stance 判断 → calc_position → buy`
  - 安全护栏：Pi 窗口避开、早盘冷静期、每日上限 3 笔、单票 1 次/日、午后额外涨幅/分位检查
  - 硬拦截标的不入池，red 立场不建仓
- **新增 `backend/app/api/pool.py`**（~90 行）：候选池管理 API
  - `GET/POST/DELETE /pool/candidates` + `POST /pool/refresh`
- **`backend/app/api/indicator.py`**：`check_entry_filters` 返回前自动调用 `maybe_capture` 入池

### 🔄 Pi 提示词更新

- **`backend/app/db/prompt_seeds.py`**：候选池指令改为"自动建仓通知"模式
  - 🟢 已自动建仓的标的告知 Pi，不重复买入
  - ⏳ 等待回调的标的由监控器处理，Pi 不干涉
  - 明确标注监控器安全护栏供 Pi 参考

### ⚖️ 监控器生命周期对齐

三个监控器现在统一受 scheduler 管理，随首个交易任务启动、随尾盘任务停止：

| 监控器 | 之前 | 现在 |
|--------|------|------|
| StopLossMonitor | scheduler 管理 | 不变 |
| PositionTierMonitor | main.py 常驻 | scheduler 管理 |
| CandidatePoolMonitor | —（新建） | scheduler 管理 |

- **`backend/app/services/scheduler_service.py`**：`_execute_task_wrapper` 中统一启停

### 📊 加仓监控日志增强

- **`backend/app/services/position_tier_monitor.py`**：
  - `_check_all_positions` 返回统计摘要（total / triggered / hold / executed / blocked）
  - 每轮循环都输出日志：`[TierMonitor] 第N轮: 持仓X只 | 触发Y只 | 未达标Z只 | 已执行W只 | 拦截V只`
  - 不加仓时也有日志输出，不再静默

### ⏱️ 轮询间隔错峰

三个监控器使用质数间隔 + 初始偏移，避免同时冲击实时价格接口：

| 监控器 | 间隔 | 初始偏移 |
|--------|:---:|:---:|
| StopLossMonitor | 31s | 0s |
| PositionTierMonitor | 33s | 10s |
| CandidatePoolMonitor | 37s | 20s |

### 🔍 健康检查扩展

- **`backend/app/main.py`**：`/api/v1/health` 新增 `candidate_pool_monitor` 状态字段

### 📝 涉及文件

- `backend/app/services/candidate_pool.py`（新建）
- `backend/app/services/candidate_pool_monitor.py`（新建）
- `backend/app/api/pool.py`（新建）
- `backend/app/api/indicator.py`（修改）
- `backend/app/services/scheduler_service.py`（修改）
- `backend/app/services/position_tier_monitor.py`（修改）
- `backend/app/services/stop_loss_monitor.py`（修改）
- `backend/app/db/prompt_seeds.py`（修改）
- `backend/app/main.py`（修改）

---

## [1.5.2] — 2026-06-25（加仓代码化 + 职责边界澄清）

本次更新将三级加仓判断从 AI 手中收回，改为代码层自动执行，解决 22 笔交易中加仓 0 次的系统性问题。同时明确了盘中扫描报告的 AI 职责边界。

### 🤖 加仓代码化：PositionTierMonitor

- **新增 `backend/app/services/position_tier_monitor.py`**（~540 行）：独立后台线程，与 StopLossMonitor 并行运行
- **三层架构全部落入代码层**：
  | 层 | 功能 | 触发 |
  |:---:|:---|:---|
  | 第 1 层 | 层级评估 | 浮盈 ≥ 1% → confirm / 浮盈 ≥ 3% → sprint |
  | 第 2 层 | 门控仲裁 | Pi立场/回撤＜5%/连亏＜3/保护线/趋势确认/单日≤3次 |
  | 第 3 层 | 自动执行 | 计算股数 → 保护线末检 → 下单并更新层级状态 |
- **时间窗口控制**：早盘 09:30-09:45 冷静期 + 尾盘 14:30 后禁止加仓
- **层级状态持久化**：JSON 文件 + 通知日志 `tier_notifications_{date}.jsonl`

### 🛡️ 保护线与加仓联动

- 确认仓 → T1 保本线（跌回成本价拦截加仓）
- 冲刺仓 → T2 保护线（成本+X%，X 由近5日日均振幅决定）
- 浮盈距离保护线不足 0.5% → 不加仓

### 📋 Prompt 职责重新分配

- **`prompt_seeds.py` TRADE_SYSTEM_PROMPT**：
  - 删除 AI 加仓判断职责，"可加"改为"代码自动，AI 只记录"
  - 新增 `6.0 接收代码层通知` 节（EXECUTED / BLOCKED / SKIPPED / EDGE_CASE）
  - 新增"代码硬性保护规则"节（8 条加仓门控明文化）
  - 交易报告模板中增加代码层加仓/拦截示例行
- **`scheduler_service.py` 盘中扫描 `_call_pi_analysis`**：
  - 新增"职责边界"声明：AI 只负责市场环境判断，不负责个股止盈/减仓/加仓建议
- **`scheduler_service.py` 交易窗口指令**：
  - 9:53 / 10:35 / 13:35 三个窗口删除"可考虑加仓"，改为"查看 PositionTierMonitor 通知"

### 📱 QQ 通知分类

- 代码层自动加仓 → `🟢 **自动加仓**`（新增分类，区别于 `🟢 **买入成交**`）
- 修改 `marcus_trade.py` `_notify_buy`：按 `[TierMonitor自动加仓]` 前缀自动分流

### 🔧 集成

- **`main.py`**：启动时 `start_tier_monitor()`，关闭时 `stop_tier_monitor()`，`/health` 端点新增 `position_tier_monitor` 字段

### 📝 涉及文件

- `backend/app/services/position_tier_monitor.py`（新建）
- `backend/app/db/prompt_seeds.py`
- `backend/app/services/scheduler_service.py`
- `backend/app/core/trading/marcus_trade.py`
- `backend/app/main.py`

---

## [1.5.1] — 2026-06-15（技术指标幻觉修复 + 数据时效标注体系）

本次更新解决了 Marcus Agent 在没有技术指标工具可用的情况下凭空编造 KDJ/MACD/RSI 信号的幻觉问题，同时建立了完整的数据时效标注体系。

### 📊 盘中实时技术指标计算

- **新增 `core/realtime_indicators.py`**：KDJ(9,3,3)/MACD(12,26,9)/RSI(6/12/24)/MA(5/10/20) 盘中实时估算算法
  - 数据源：腾讯 qt.gtimg.cn 实时 OHLCV + Tushare daily 历史日K线（≥35条）+ Tushare stk_factor_pro 前日确认值锚定
  - 所有返回值标记 `data_source='intraday_estimate'`，与盘后确认值明确区分
  - 可靠性 ⭐⭐（盘中估算），收盘后 Tushare 实际值误差通常 5% 以内
- **新增 `GET /indicator/realtime/{symbol}` API 端点**：并行获取腾讯行情 + Tushare daily + stk_factor_pro
  - `realtime`：盘中估算值
  - `historical`：最近 N 日 Tushare 盘后确认指标作基准对比
- **新增模型** `RealtimeIndicatorItem` / `RealtimeIndicatorResponse`（`backend/app/models/market.py`）

### 🔧 Agent 工具链补全

- **`agent.py` 新增 3 个工具**：
  | 工具 | 数据类型 | 数据源 | 可靠性 |
  |------|----------|--------|:------:|
  | `get_kline` | 日频·非实时 | Tushare daily 盘后 | ⭐⭐⭐ |
  | `get_technical` | 日频·非实时 | Tushare stk_factor_pro 盘后确认 | ⭐⭐⭐ |
  | `get_realtime_indicators` | 实时·盘中估算 | 腾讯行情+Tushare历史 | ⭐⭐ |
- **`format_result_for_llm`** 新增 3 个工具的专用格式化逻辑，含指标信号标记（金叉/死叉/超买/超卖）
- **`TOOL_IMPLEMENTATIONS`** 映射补全，桥接到后端 API

### 🛡️ System Prompt 幻觉防护

- **禁止编造规则**：KDJ/MACD/RSI/MA/BOLL 等指标必须通过工具获取实际返回值
- **来源标注强制**：引用指标时必须附带 `[盘中估算/未确认]` 或 `[盘后确认/T-N日]` 标签
- **过时信号警告**：昨日盘后金叉/死叉在今天开盘后可能已失效，需盘中重新确认
- **建仓决策层级**：盘后确认信号为主（⭐⭐⭐），盘中估算为辅（⭐⭐）

### ⏱️ 数据时效标注体系

- **工具描述三分类**：`[实时]` / `[日频·非实时]` / `[实时·盘中估算]`，贯穿 agent.py TOOLS 定义、Pi server tools.ts、index.ts System Prompt 三处
- **Pi server 动态日期注入**：每次会话 `getOrCreateAgent` 在 systemPrompt 前动态注入 `当前时间: 2026-06-15 21:18 (周一)`
- **日频数据截止日期**：所有日频工具（get_kline/get_technical/get_daily_kline_qfq）输出首行标注 `数据截止日期: YYYYMMDD（最近收盘日）`
- **Pi server 工具分组更新**：`CHAT_TOOLS` / `REFLECT_TOOLS` 补全 `getRealtimeIndicatorsTool`

### 📝 涉及文件

- `core/realtime_indicators.py`（新建）
- `backend/app/models/market.py`
- `backend/app/api/indicator.py`
- `backend/app/api/agent.py`
- `servers/pi-server/src/tools.ts`
- `servers/pi-server/src/index.ts`

---

## [1.5.0] — 2026-06-15（仓位规则优化 + 群聊模式升级 + 止损体系完善）

本次更新基于专家组群聊复盘暴露的 23 条仓位规则过度保守、Pi vs Scan 立场冲突、Yellow 下购买力归零等问题，进行了外科手术式精准修复。

### 📊 仓位规则优化（专家组共识驱动）

- **现金底线**：40% → **25%**（所有 stance），释放约 15% 购买力
- **Yellow 仓位上限**：40% → **50%**（修复"死锁"）
- **单票上限分档**：统一 15% → 按信号强度 **10%/18%/25%** 三档
- **二次建仓**：硬性"前仓浮盈"条件 → **三级加仓架构**（试探≤10%→确认≤18%→冲刺≤25%）
- **V反确认间隔**：10分钟 → **5分钟**
- **涨幅>3%等待**：15-20分钟 → **8-10分钟**
- **拒绝次数上限**：8次 → **10次**
- **涨停占比折扣**：>30%→×0.5 → **>40%→×0.7**
- **资金流出折扣**：×0.7 → **×0.8**
- **极端流出防御**：第3轮减仓50% → **渐进式（1轮→70%/2轮→40%/3轮→20%）**

### 🛡️ 止损体系完整修复

- **板块背离公式重写**：3x 乘法 → 差值法（个股收益 - 板块收益 < -3pp）
- **智能成本止损分级**：大盈转亏→保本离场(-1%) / 曾小盈→-3% / 从未盈利→-4% / 无HWM→-6%
- **规则 0a 锚点动态上移**：止损价 = max(阶段底×0.97, HWM×0.90)
- **规则 3 大盘改为相对表现**：个股 vs 大盘差值判定
- **规则 2 铁律二回吐收紧**：≥8%→+6% / ≥5%→+3.5%
- **早盘冷静期**：09:30-09:45 不执行卖出（该窗口统计胜率 0%）
- **规则冲突 SOP**：优先级链 + 前置拦截文档化
- **止损日志表**：PostgreSQL `stop_loss_log` 表 + 审计能力

### 👥 群聊模式升级

- **去周报化**：5 个 Panel Prompt 去掉"本周/下周"时间限定
- **两套 Prompt**：review（复盘，带模板）/ chat（聊天，问题驱动），自动按场景选择
- **数据采集开关**：跳过 Phase 0，专家各自获取数据
- **去掉旧报告**：移除 `get_panel_history` 自动采集，避免已修复 bug 被重复报告
- **逆向质疑者铁律**：新增"先理解设计意图，再判断意图是否成立"规则
- **群聊模式不限复盘**：开放所有话题讨论

### 🎛️ Pi vs Scan 立场冲突解决

- **Pi 是唯一决策者**：删除 90 行偏离检测代码，Scan stance 仅作参考
- **Scan API 调整**：`market_stance`/`position_limit` 优先取 Pi 值
- **Yellow 可买入**：删除"找不到2个做多理由→red"限制
- **仓位折扣下限**：从 10% 提升到 20%，确保 Yellow 够买 2 支

### 🔧 数据基础设施修复

- **K线 pro_bar**：`ts.pro_bar()` API 名修正（Docker 容器 tushare 版本兼容）
- **技术指标字段**：`stk_factor_pro` fields 参数 + `_qfq` 后缀修正 + RSI 字段名修正
- **东财接口盘前过滤**：9:30 前跳过东财实时，走 Tushare
- **HWM 数据流**：监控器每轮主动更新 `_ensure_hwm()`
- **改进追踪系统**：`improvement_tracker.py` JSON 持久化

### 🔄 提示词工具集成

- **新工具注册**：`get_trade_advice` / `get_fibonacci_levels` / `get_daily_channel` 加入 System Prompt
- **触发导向描述**：工具描述明确使用场景，AI 知道何时调用
- **工具使用优先级**：Prompt 指导"先调 get_trade_advice 再看详情"

---

## [1.4.0] — 2026-06-14（牛股计算器策略 + 腾讯行情 + 止损启用）

### 📐 牛股计算器策略体系

引入完整的"四维交易体系"（空间+时间+防守+逻辑自洽），新增 3 个 AI 工具 + 1 个 API 端点：

- **斐波那契回撤 `get_fibonacci_levels`**：自动提取阶段顶/底，计算 0.382/0.618/0.786 三个关键价位，判断当前价格所处区间
- **日内K值通道 `get_daily_channel`**：基于 K=0.98848 常数计算压力/支撑线，用于超短线入场/离场判断
- **操作建议 `get_trade_advice`**：完整决策树，根据持仓/观察模式输出格式化操作信号
  - 持仓模式：破底止损 → -6%止损 → 时间证伪 → 突破新高 → 持有
  - 观察模式：破位严禁 → 放弃极弱 → 跌破618 → 强防生死线 → 常规买点 → 高位观望
- 新增 `backend/app/api/indicator.py` API 模块（3 个端点）+ `backend/app/models/indicator.py` 数据模型

### 📈 动态顶部追踪 & 时间证伪

- `core/utils/strategy_chain.py` 新增 9 个 High Water Mark 方法
- `data/position_highs.json` 持久化持仓历史最高价
- 时间证伪规则：13 个交易日未创新高 → 自动触发离场提醒
- `portfolio` API 集成 high_water_mark / days_since_high 字段
- 时间证伪检查在尾盘/每日复盘/周度反思时自动触发 + QQ 推送

### 🛡️ 止损规则扩展

- **破底止损**：跌破阶段底部 3% → 自动卖出（牛股计算器策略）
- **成本止损**：亏损超过 6% → 自动卖出（牛股计算器策略）
- 两条新规则插入 `stop_loss_monitor._evaluate_stop_rules` 最高优先级

### 🔄 实时行情接口切换

- **雪球 → 腾讯 qt.gtimg.cn**：核心行情引擎切换
  - 免认证、无频率限制（解决 30s 轮询 IP 封禁问题）
  - 60+ 原生字段（盘口/量比/内外盘/委比等未来可扩展）
- 修改 `core/xueqiu_engine.py` 的 `get_stock_quote()` 方法
  - 新增 `_tencent_to_symbol()` / `_parse_tencent_quote()` 辅助方法
  - 对外接口完全兼容，所有下游调用方（6 个模块）无需修改
- 数据格式：腾讯 `~` 分隔文本 → 自动映射为雪球兼容 dict

### 🟢 止损监控正式启用

- 解除 `scheduler_service.py` 中的启动/停止注释
- 首个早盘交易任务自动启动监控器 → 尾盘任务自动停止
- Executor 自动注入（`MarcusVNPyExecutor` 绑定到监控器）
- 6 条止损规则按优先级自动执行：破底 → 成本-6% → 板块背离 → 铁律二 → 大盘动态 → T+1 保护

### 🔧 工具定义三处同步

- 工具定义同步三处：`servers/pi-server/src/tools.ts` / `frontend/src/components/ChatContainer.tsx` / backend API
- 新工具加入 `CHAT_TOOLS` / `TRADE_TOOLS` / `REFLECT_TOOLS` 三个分组
- 前端 `TOOL_LABELS` 和 `COLLAPSIBLE_TOOLS` 同步更新

---

## [1.3.0] — 2026-06-13（群组复盘驱动改进）

本次更新基于本周群组复盘暴露的 11 个问题 + 代码架构审计发现的 5 个缺口，进行了从代码层风控硬约束到 Prompt/SOP 流程的全面改进。

### 🛡️ 风控增强（代码层硬约束）

- **回撤熔断**：总回撤 ≥ -5% 硬拦截所有买入（不再依赖 AI 自觉）
- **连续亏损熔断**：连续亏损 ≥ 3 笔自动停止当日买入
- **T+1 硬拦截**：查询 `trades.db` 今日买入记录，当日不可卖出（代码层保护）
- **极端流出日防御**：全市场主力净流出 > 800 亿 + 连续 3 轮确认 → 尾盘对所有非 T+1 持仓强制减仓 50%
- **仓位利用率检查**：Pi 建议仓位 > 实际 3 倍时注入警告
- 新增 `stop_loss_monitor.py` 实时止损监控模块（v1.4.0 随腾讯接口切换正式启用）

### 📊 东财 API 缓存回退机制

- 新增 `core/utils/eastmoney_cache.py` 统一缓存模块
- 所有东财实时 API 调用成功后存入按日 JSON 缓存
- requests/urllib/curl 全部失败时返回本日上一次缓存数据，并标注时点
- 覆盖：主力净流出（`marcus_trade.py`）+ 概念/行业板块资金流（`em_sector_flow.py`）
- 调度器启动时自动清理 3 天前缓存

### 📝 Prompt & SOP 优化

- V反/假突破两次确认规则（间隔 ≥ 10 分钟，两轮扫描）
- 拒绝次数上限制度化（≥ 8 次 → 终止当日建仓，转「只卖不买」）
- 跨周模式识别（CROSS_WEEK 标记，防止「浮盈→亏损」跨周复现）
- 代码层硬风控说明（AI 无需手动判断回撤/T+1/连续亏损）
- 新增 `scripts/reseed_prompts.py` 一键同步 Prompt 到数据库

### 🔄 策略链增强

- 止损阈值从 -5% 对齐新 SOP 的 -2%
- 新增「浮盈→亏损」模式检测 + `tighten_stop` 动作
- 新增 `_check_cross_week_pattern()` 跨周模式对比逻辑

### 📈 过滤器拒绝率追踪

- 新增 `log_filter_rejection()` / `get_filter_rejection_stats()` 日志机制
- 接入技术面三项硬过滤（MA5 / MACD / 量比）+ AI 假突破过滤
- 写入 `filter_rejections.jsonl`，周复盘可直接统计最严过滤器

### 🧹 代码架构清理

- `core/utils/marcus_trade.py` → 轻薄重导文件，唯一来源为 `backend/app/core/trading/marcus_trade.py`
- 消除两份 `MarcusVNPyExecutor` 维护不同步风险

---

## [1.2.0] — 2026-06-13（交易与数据增强）

### 🔧 K线 & 市场数据

- 拆分日K线为未复权与前复权两个独立接口
- K线接口支持复权方式参数，默认前复权
- 修复 16 点后及周末东财实时接口调用（自动跳过降级到 Tushare）

### 🤖 Pi Agent & 专家组

- 新增历史复盘查询工具（`get_session_messages`）
- 优化专家组提示词（panel mode）
- 聚焦用户问题并增加调试日志
- 改为逐条推送专家发言实时气泡

### 🎛️ 前端 & SSE

- 专家组群聊讨论模式及 SSE 流式支持
- Panel SSE 流直连 pi-server Nginx 代理
- MiniMax 切换至国内站并重构流式转发
- 修复刷新时 UI 消息列表可能不同步
- 修复加载标记位置并即时发送 SSE 启动事件

---

## [1.1.0] — 2026-06-12（基础设施 & 策略优化）

### 🛠️ 基础设施

- PostgreSQL 支持 + Prompt 动态管理（数据库表 + CRUD API）
- Docker Compose 多服务编排（postgres + backend + piserver + frontend）
- Nginx 前端反向代理 + SSE 流代理

### 📈 交易策略

- 反思模式重构为专家组群聊讨论（风控审计师 + 右侧交易员 + 量价分析师 + 板块联动分析师）
- 加速降级检测与策略规则优化
- 锁仓解除条件（防止高开低走误判）
- 行业聚焦、买入回踩与止盈保护规则
- 涨停股与止损补位策略优化
- 龙头优先硬约束 + 午后建仓模式
- Pi 交易模式启用高思考等级模型

---

## [1.0.0] — 2026-06-11 及更早

- 🎉 项目初始化：Marcus AI Trading Platform
- VN.PY 模拟交易引擎集成
- 雪球/akshare/Tushare 多数据源
- Pi Agent HTTP Server（DeepSeek/MiniMax API）
- 定时调度器（盘前扫描 / 盘中交易 / 尾盘收盘 / 复盘）
- QQ Bot 通知推送
- 前端监控面板（React + Vite）


## [1.7.0] 2026-09-02（晚）· 交易执行层狼大化 + 正T三档落地

### 做T体系扩展（三档正T买点 + 高抛闭环）
- 正T语义语料核实（docs/zt-zhengT-semantics-report.md）："大盘带下来"=持仓个股被拖累的盘中低点（每天级），非上证整日-2%（年~10次）；方法=挂前日低点+缩量
- 信号对比回测（apps/main_line/backtest_zt_signal_compare.py）：C 大盘5min分时急杀0.4%（16天 T+1 +0.82%）/ A 个股触前日低点+缩量（133天 +0.78%）为合理频率档
- 生产新增 253（custom_m5dump, index.m5_dump≥0.4）/ 254（custom_prevlow, dip_prev_low+vol_ratio∈(0,0.7]）条件；t_expr/t_monitor 注册 index.m5_dump、quote.dip_prev_low（30s TTL）
- 高抛闭环修复：狼大形态条件改**非消费式持续腿**（触发保持active+armed，5分钟冷却防刷）；250 回场；买腿加黄线护栏（quote.current>quote.average）；卖腿一次清T仓（volume=sellable-100）

### 交易执行层狼大化
- 做T底仓保护（backend/app/api/trades.py）：做T标的卖出最多卖持仓-100（Pi place_order 路径不经 t_gateway 白名单，此前可卖光底仓）
- auto_trade 5任务恢复 enabled（09-02下午，受底仓保护约束）
- 交易提示词重构（backend/app/db/prompt_seeds.py→DB id=37 reseed）：SOP加【第零步浪型策略(交易主基调)】每报告必输出；删"盈亏比1:1.5"；calc_position 止损8%→3%逻辑止损
- trade_graph：震荡/趋势注入对齐月度门控（震荡日短期层只做T不新开，删旧建仓60%/加仓40%时段指令）
- 盘中扫描（jobs/market_scan.py）+盘前诊断（jobs/morning_diagnosis.py V2.1）注入🐺狼大视角；盘中扫描清死代码521行
- 部署：docker-compose 加 ../jobs:/app/jobs bind mount（镜像COPY jobs会遮旧版）

### 修复/坑
- vol_ratio 数据缺失=0.0 令 ≤0.7 恒真 → 254 误触发买入（已加 >0 前置）
- prompt_seeds.py 是权威源，改DB不改源=重建丢修改
- 长中文 patch 脚本 TS/Python 双层转义易错（N')/多引号），用 String.raw + chr(92) + 文本锚点稳定


## [1.7.1] 2026-09-02（晚）· 选股链路调度自动化核实

- **根因查明**：08-31 服务器 config/tasks.yaml 曾含 main_line_judge 定时任务（logs/main_line_judge/675cbcdb.json，08:00:00 触发 success），09-02 本地 20 任务配置同步覆盖服务器时丢失；09-01/09-02 的 wave_state/position_class_result/stock_confirm_result 刷新均为手动运行（无对应调度日志），非代码内/DB/crontab 调度。
- **修复**：config/tasks.yaml 补 3 个定时任务并本地+服务器同步——main_line_judge（周一 8:00，apps/main_line/main_line_judge.py）/ wave_judge（8:10，wave_agent.py）/ position_judge（8:20，position_judge.py：position_class→low_logic_agent→stock_confirm_judge 链式）。
- 备份：/opt/marcus-platform/config/tasks.yaml.bak_20260902_20task；worker 已重启，23 任务加载/调度器运行验证通过（jobs_count=23, enabled=23），新脚本容器内 import 冒烟 OK。
- 坑：改 tasks.yaml 必须以本地 config/ 为权威并同步服务器+重启 worker，防止再次被本地旧配置覆盖（曾两次丢失 08-31 已加条目）。


## [1.8.0] 2026-09-02（晚）· P2 主线内轮动/产业链形态第一轮闭环

- **语料→逻辑**：docs/p2-rotation-wolf-logic.md（轮动两态/高低切语境/产业链传导/去弱留强/R7调仓），22+7 例 case 表 docs/p2-rotation-cases.md。
- **验证**：wave_agent 历史重放 23/23 全齐 + position_class 时点回放；27 case 一致率 OK20/na7/0 错；A1/C3/D5 预期修正、B3 复核为 defense 期主线内调仓 → MISMATCH 清零。
- **规则 code 化**：rotation_gate.py（五分支 gate v2）+ test（19场景）+ regress_rotation_gate.py（真实 wave/position 27case 端到端 19/19）双绿；trade_graph 注入轮动门控 context + LOW 埋伏候选拥挤过滤。
- **细分宇宙+真实拥挤度**：fund_portfolio_holdings（Q2 真实公募，642行/274股）+ stock_concept_map 成分 → build_crowding.py 聚合 → rotation_universe.py 双维打分（拥挤×位置空间，存储=拥挤但有空间）；L1 科技/AI总集(META)+AI应用/AI终端 + L2 芯片/光通信/算力/液冷/存储/材料/铜缆电源 + 非科技5组；词表冻结 v1（docs/p2-rotation-universe-config.md）。


- **调度**：stock_pool_refresh / rotation_universe_classify 移至周日（8:00/8:05），首次自动全量分类、之后增量+清理失效概念；季度 fund_crowding_refresh（1/4/7/10月25日 8:30）。
- 生产：worker 25 任务；轮动门控已进 Pi prompt（拥挤无空间/可埋伏/拥挤但有空间/高拥挤代表个股）。
- 剩余（下一批）：材料大级别买点执行链路（R7日历）、双维分类稳定性回测、拥挤名单下单硬过滤（当前为候选过滤+Pi软约束）。


## [1.9.0] 2026-09-03（早）· P2 风控主体 + 轮动遗留子项收口

- **P2 风控**：risk_gate v1(R-R1黑名单/R-R2财报/R-R3两融/R-R4拥挤, 11场景) → risk_flags DB(forecast/express/ST 结构化, 候选即时查模式 --stocks, 全市场ST 206) → check_entry_filters 硬拦(业绩雷 000586 E2E blocked, 需 downgrade_multiplier=0 教训) → trade_graph 风控门控 prompt；系统性风险联动开关 systemic_risk(银行512800双头+科创50不反→防御 / 大光破位大黑K→止盈) + build_systemic_inputs 采集器(工作日15:05, tasks 26)；Pi建仓SOP"先过滤后暴雷校验"reseed。
- **P2 轮动遗留 3 项全完**：①材料大级别买点链路 material_entry.py(R7业绩月+wave转build/筑底side+指数&材料confirm, 实盘 wait)；②双维分类历史稳定性回测 backtest_rotation_quadrant.py(2025-12~2026-08 13时点, 相邻转换率14.9%, 拥挤维度Q2近似)；③拥挤名单下单硬过滤 crowding_blacklist(23概念1045股) 进 check_entry_filters。
- 文档：docs/p2-risk-wolf-logic.md；overview/context 已更新(2026-09-03)。

## [1.10.0] 2026-09-03 · 拥挤过滤下沉个股级（PIT 基金持仓 + 前向重测验证）

- **动机/验证**：旧 crowding_blacklist 按"拥挤子方向整概念成分"硬拦 1045 只，误拦低位/轻仓个股（语料事件 E06-E13 前向重测：E10 国算 +10.4% / E11 材料 +44.2% / E12 半导体低吸 +34.3% 全被旧过滤挡下）。
- **Phase0/1（PIT 数据地基）**：确认 fund_share 支持历史 trade_date、fund_portfolio 按 ann_date 分级披露；fund_portfolio_holdings 扩至 20250630/20250930/20251231/20260331/20260630；每事件日按 T-1 top60 基金 + ann_date<=事件日 + 每基金 top10(按mkv) 生成 data/crowding_pit/stock_crowd_<date>.json（apps/main_line/build_fund_pit.py）。
- **Phase2（前向重测）**：apps/main_line/backtest_crowding_stock_level.py，40 目标股 qfq+position 特征 → old vs new 矩阵（docs/crowding-stocklevel-event-recheck.md）；推荐阈值 N_funds≥4 & sum_float≥1%；旭创/中微等真核心拥挤仍拦（风控分歧非误拦）。
- **Phase4（线上集成）**：rotation_universe.build_crowding_blacklist v2 → 仅拦公募核心拥挤个股（members 1045 → blocked 17），输出 symbols_detail{symbol,n_funds,float_pct,reason}；backend check_entry_filters 读取 per-symbol reason（“拥挤无空间(个股级rotation): n_funds=.., sum_float=..%”），并加个股空间豁免（crowd core + LOW/MID回踩 → 降级 review/probe 而非硬拦）。


## [1.11.0] 2026-09-03 · 买点对齐狼大：误加技术/时间门控改为软约束（可回退）

- 审计结论(docs/wolf-extra-logic-audit.md)：Wolf 语料没有 MA5>MA20/60分MA10>MA30/KDJ/RSI/CCI/射击之星/午后禁开仓等旧技术栈逻辑，且与他尾盘买/E05-E08低吸冲突。
- check_entry_filters 默认进入 Wolf 对齐模式：60分/日线 MA 结构、MACD死叉、RSR、KDJ高位死叉、Layer3超买形态(射击之星/看跌吞没/RSI/KDJ/CCI) 全部改为提示或≤0.5降级，不再硬拦；午后13:00后/尾盘不再禁新开仓。
- 数据可用性 fail-closed(60分MA缺失/日内分位缺失/主力资金缺失=自动通道跳过+QQ)保持不变。
- 拥挤核心硬拦(n_funds≥4&float≥1%+高位无空间)保留为风控分歧(非Wolf逻辑但属于风险护栏)；LEGACY_TECH_GATES=1 可一键恢复旧硬门槛(回退用)。
- backend已重启healthy；smoke: 688012 grade=blocked 由拥挤风控触发，MA/时间/形态均显示Wolf口径软提示。


## [1.12.0] 2026-09-03 · P2 宏观采集器 v1（macro_state.json）

- 探测结论: worker已装akshare, bond_zh_us_rate()可用(1990-12-19~2026-09-02, 含CN/US 2/5/10/30Y); FRED超时/Yahoo429/东财RemoteDisconnected; DXY实时可用新浪DINIW(历史待自建快照)。
- apps/main_line/build_macro_state.py: 采集 CN/US收益率 + 美元指数实时 + 两融(余额/20d变化/净买/分位) + GJD宽基份额(510300/510050 5d/20d) + 北向5d/当日 → data/macro_state.json。
- config/tasks.yaml 新增 macro_state_collector(工作日15:06, tasks 26→27); 已同步服务器并重启worker。


## [1.13.0] 2026-09-03 · macro_state v2：Wolf 四类开关推导 + trade_graph 接入

- build_macro_state.py 增加 _derive_switches：从原始值推导开关(flags/detail + macro_switches_text)：
  ①崩盘清单-债市异动(us/cn 30Y单日>=+0.10/0.15) ②美债10Y-2Y倒挂 ③两融热钱(20d>=3%+净买>0)/杀杠杆(20d<=-5%)
  ④GJD护盘(份额20d>0)/撤退(份额20d<-2%) + 北向5d正负；
- trade_graph.py 新增 _read_macro_context()（宏观快照+Wolf开关文本），node_fetch_context 注入 macro_context，Pi prompt 在 rotation/risk 之间输出；
- 实测 2026-09-03: flags=[gjd_withdraw, north_in] → 文本“GJD撤退不抢反弹 + 北向流入允许跟主线”；宏快照含 CN30=2.14/US30=5.27/DXY=99.48/两融26610亿/GJD份额20d负；
- worker 已重启 healthy, tasks=27; macro_state_collector 15:06 自动刷新含开关。


## [1.14.0] 2026-09-03 · P2 Gate Step1：wave/systemic/macro 统一闸门接入 check_entry_filters

- 新增 backend/app/services/p2_entry_gate.py：统一返回 hard_block/multiplier/reasons；wave defense/exit → 硬拦新开仓；systemic level>=2 → 硬拦；macro margin_burst → 硬拦，lhb_foreign_sell/gjd_withdraw/yield_spike → 降级0.5；P2_GATE_MODE=0 dry-run。
- indicator.py 在 crowding 后调 p2_gate_check；响应新增 p2_gate_details（软/硬均可见原因）。
- 效果：auto 通道（candidate/long_term monitor 只调 check_entry_filters）自动获得 wave/systemic/macro 硬拦（此前只有 Pi/trade_graph 有 wave 硬拦）。
- 实测 SZ300054：grade=probe_only mult=0.5，p2_gate_details=GJD撤退降级0.5。

## [1.15.0] 2026-09-03 · 修复：狼大做T持续腿跨日丢失（监控条件消失）

- **现象**：2026-09-03 交易报告 SH603259 无 t_conditions；DB 中 249/250/252/253/254 五条狼大持续腿 status=active 但 trade_date=20260902，无 09-03 行。
- **根因**：t_conditions 按交易日建行(唯一键 account+symbol+trigger_kind+trade_date)；t_monitor._round 每轮只读"当日"active 条件；狼大持续腿为非消费式（命中不销毁、5分钟冷却）且自动维护已停（只留狼大做T），没有任何机制把它们结转到新交易日 → 跨日后昨日行日期不匹配当日查询，报告显示"没有监控条件"。
- **修复（backend/app/services/t_monitor.py + t_db.py）**：
  - t_db.list_active_conditions 支持 before_trade_date（取某日之前仍 active 的条件）；新增 list_condition_keys（某账户某日全部条件键，任意状态）。
  - TMonitor 新增 _roll_wolf_legs()：启动时与每日交易日切换时幂等结转——把 today 之前仍 active 的狼大表达式腿按 (symbol, trigger_kind) 取最近一日复制到当日（保留表达式/价格/止损/publisher），今日已有同键行(含人工停用)则跳过，成功后旧日源行归档 expired。
  - 结转不依赖 T_MONITOR_AUTO_MAINTAIN（自动维护仍默认关闭）。
- **验证**：部署重启后日志 "[TMonitor] 狼大持续腿跨日结转 5 条 → 20260903"；DB 生成 09-03 新行 256-260(5条 active，表达式完整，trigger_count 归零)；旧 249/250/252/253/254 → expired；10:08:45 monitor 命中 high_sell(#69)/custom 黄线(#70)，因可卖 T仓=0 自动执行 blocked（底仓100保护生效，无错单）。


## [1.15.0] 2026-09-03 · P2 Gate Step2：方向感知 + 命中日志 + 扫描展示

- p2_entry_gate.py: lhb_foreign_sell 方向感知（data/p2_macro_direction_map.json，海外链/红利核心概念命中才降0.5，其它不拦）；每次命中写 data/p2_gate_log.jsonl（symbol/ts_code/multiplier/reasons）。
- market_scan.py / morning_diagnosis.py 狼大视角新增宏观/机构段（CN/US30Y+DXY+两融+GJD+开关flags）。
- 实测 SZ300054: probe_only/mult0.5，p2_gate_log 已落盘。


## [1.16.0] 2026-09-03 · P2 Gate 每日观察报告任务

- jobs/p2_gate_daily_report.py：聚合当日/近N日 p2_gate_log.jsonl（硬/软、wave/systemic/margin_burst/lhb/gjd/yield/other、top标的、样本reasons）→ data/p2_gate_daily_report.json + stdout。
- config/tasks.yaml 新增 p2_gate_daily_report（工作日15:20，tasks 27→28）；已同步服务器并重启worker。
- 实测2026-09-03: hits=1 soft=1 gjd_withdraw, top=300054。


## [1.17.0] 2026-09-03 · rotation 双维分类 13 时点真 PIT 重跑（修正季度近似口径）

- **数据**：apps/main_line/build_fund_pit.py 新增 --dates 支持，为 13 个双周时点逐点生成 PIT 拥挤快照（历史 fund_share T-1 top60 + ann_date<=时点 + 每基金 top10按mkv）→ data/crowding_pit/stock_crowd_<date>.json。
- **聚合/重跑**：新增 apps/main_line/backtest_rotation_quadrant_pit.py：SUB_UNIVERSE 概念白名单×stock_concept_map 把每点 PIT 快照聚合成子方向拥挤（avg_float_per_held/n_held/sum_float），位置空间复用 backtest_rotation_quadrant.py → data/rotation_quadrant_history_pit.json + data/crowding_pit/rotation_crowd_pit_<YYYYMMDD>.json。
- **结果**：13 时点总相邻转换率仍 23.4%（36/154，与季度近似口径一致），但 51/180=28.3% 格子变化（一致率 71.7%）：修正 2026-04-09 季报前视（Q1 ann 04-21 前仍用 Q4-25）；07-22/08-11 AI应用/国算/光通信/铜缆/金融 → 可埋伏（公募核心拥挤切向存储/芯片/材料）；01-12/01-30 金融/电力/光通信在 PIT top10 口径下更拥挤。
- 报告 docs/p2-rotation-quadrant-pit-report.md；WOLF_TASKS_OVERVIEW §1.11 + 剩余清单去除该项。
