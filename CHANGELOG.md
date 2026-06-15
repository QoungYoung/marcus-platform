# 更新日志

本文档记录 Marcus AI Trading Platform 的主要变更。

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
