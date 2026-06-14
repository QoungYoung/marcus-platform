# 更新日志

本文档记录 Marcus AI Trading Platform 的主要变更。

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
