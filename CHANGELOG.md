# 更新日志

本文档记录 Marcus AI Trading Platform 的主要变更。

---

## [1.3.0] — 2026-06-13（群组复盘驱动改进）

本次更新基于本周群组复盘暴露的 11 个问题 + 代码架构审计发现的 5 个缺口，进行了从代码层风控硬约束到 Prompt/SOP 流程的全面改进。

### 🛡️ 风控增强（代码层硬约束）

- **回撤熔断**：总回撤 ≥ -5% 硬拦截所有买入（不再依赖 AI 自觉）
- **连续亏损熔断**：连续亏损 ≥ 3 笔自动停止当日买入
- **T+1 硬拦截**：查询 `trades.db` 今日买入记录，当日不可卖出（代码层保护）
- **极端流出日防御**：全市场主力净流出 > 800 亿 + 连续 3 轮确认 → 尾盘对所有非 T+1 持仓强制减仓 50%
- **仓位利用率检查**：Pi 建议仓位 > 实际 3 倍时注入警告
- 新增 `stop_loss_monitor.py` 实时止损监控模块（当前未启用：30秒轮询会触发雪球 IP 限流）

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
