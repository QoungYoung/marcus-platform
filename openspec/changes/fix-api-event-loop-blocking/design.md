## Context

现状约束（动机见 proposal.md - Why，需求见 specs/api-responsiveness/spec.md）：

- backend 以单进程 uvicorn 运行（启动命令无 --workers），所有 HTTP 请求共享一个事件循环。
- backend/app/api/golden_pit.py 的 /status、/history、/snapshots、/tech-status 均为 async def，却直接同步调用 GoldenPitService 的阻塞方法；_get_status_from_api 内部用 ThreadPoolExecutor(...).result() 等待外部调用，阻塞的仍是调用线程（事件循环）。
- 外部数据源超时现状：Tushare DataApi 默认 timeout=30s；ArkVol urlopen timeout=60s；Xueqiu/腾讯行情路径未确认有界超时。DB 快照历史不足 60 天时 get_status() 必走慢速 API 回退路径（串行/半并行抓取大量行情与 K 线）。
- backend 进程内还有 vnpy bridge、scheduler、监控 daemon 线程（启动日志可见），多 worker 需防重复启动。

## Goals / Non-Goals

**Goals:**
- 事件循环上不再执行同步重活：慢 handler 迁移到线程池执行。
- 全部外部数据源调用有界超时（单次 <=10s），失败走缓存/降级，调用链总耗时受预算约束。
- /api/v1/golden-pit/status 增加响应级 TTL 缓存 + single-flight + 启动/定时预热，冷缓存页面加载不再触发重计算。
- 至少 2 个 uvicorn worker 提供并发隔离（在确认 daemon 不重复启动后启用）。
- 健康检查在重计算期间保持 1s 内有界响应。

**Non-Goals:**
- 不改变 API 响应结构/契约（可新增字段，不改既有字段语义）。
- 不重构黄金坑评分算法本身；不动 DB schema。
- 不把计算迁移到独立服务（worker 容器已是既有分工，本次仅修复 backend 进程内阻塞）。

## Decisions

### D1. async handler 的同步重活迁移到线程池
把 golden_pit.py 中纯同步调用的 async def handler 改为普通 def（FastAPI 自动放入 AnyIO 线程池执行），事件循环保持空闲；确需保留 async 签名的地方用 starlette.concurrency.run_in_threadpool 包裹同步调用。
- 备选 A：保留 async + loop.run_in_executor —— 等价但样板更多。
- 备选 B：handler 内 await asyncio.wait_for 设总超时 —— 只对真 async 调用有效，对线程池内同步阻塞无法中断，仅作兜底。
- 结论：D1（普通 def / run_in_threadpool）+ 服务层 deadline 兜底。

### D2. 服务层 deadline + single-flight + TTL 缓存（GoldenPitService.get_status）
- 新增 get_status(ttl=300s, deadline=5s)：缓存命中直接返回；未命中时加 single-flight 锁（threading.Lock），并发请求共享一次计算。
- 计算过程各外部调用受 D3 超时约束；若总时长逼近 deadline，返回最近一次成功快照（含 _source: stale）或最小结构，绝不无限等待。
- 预热：FastAPI lifespan 启动后台线程预热一次；worker/scheduler 的既有黄金坑定时任务（save_daily_snapshot 路径）在落库后同步刷新该缓存。
- 备选：独立缓存服务/Redis —— 收益有限，进程内缓存足够（TTL 短、单实例部署）。

### D3. 外部调用统一有界超时（<=10s/次）
- Tushare：get_tushare_pro 创建 DataApi 时传 timeout=10（client 已支持该参数），_fetch_pi_server_kline 的 fund_daily/fund_adj 调用受其约束。
- ArkVol：arkvol_service.py 中 urlopen timeout 由 60 降到 10（配置化），异常按现有 try/except 降级。
- Xueqiu / 腾讯行情：为 get_stock_quote、get_realtime_prices、行情 helper 增加显式超时（5-10s），失败返回缓存/零值并记日志。
- 约束重试：单次请求内同一外部调用最多重试 1 次，且计入总 deadline。

### D4. 多 worker 并发隔离（防御纵深）
docker compose 中 backend 启动命令改为 --workers 3。
- 前置条件：确认/实现 daemon 线程（vnpy bridge、scheduler、4 个 monitor）只在单个 worker 启动——检查 app.main lifespan 是否已有进程级守卫；若无，用 WORKER_INDEX=0 环境变量或只读锁门控，避免 3 个 worker 各起一套监控。
- 说明：进程内 TTL 缓存每 worker 各一份（可接受）；如启用后 daemon 门控不可行，则退回 1 worker + D1-D3（它们本身已消除事故根因）。

### D5. 健康检查保持轻量
/health 保持纯异步轻量实现（DB ping + 调度状态），不触碰行情/外部 API；配合 D1 后事件循环不再被业务 handler 冻结。

## Risks / Trade-offs

- [线程池仍被长任务占满（默认 40 线程）] → 外部调用 <=10s + 单请求调用数上限；为线程池占用加日志/指标，必要时调大池。
- [多 worker 重复启动 daemon 线程（vnpy/scheduler/monitor）导致重复下单/重复任务] → D4 前置条件硬性门控；未门控前不启用 --workers（D1-D3 已根除事故主因）。
- [严格 5s 返回可能给到 stale 数据] → 响应带 _source: db|cached|stale|api 字段（新增字段，兼容既有消费方）；stale 仅发生在外部源故障期，可接受。
- [降低外部超时增加降级频率] → 用缓存 + 预热 + 单次重试对冲，正常时段不受影响。

## Migration Plan

1. 先落地 D1-D3、D5（纯 backend 代码），docker compose 重建 backend（保持 1 worker）→ 验证 5 个端点与 /health 在有/无缓存下均 <1s。
2. 落地预热 + 缓存（D2）后，冷启动即测 golden-pit/status 响应时间与数据新鲜度。
3. D4 门控 daemon 后启用 --workers 3 → 观察 3 worker 下调度/监控无重复执行。
4. 回滚：保留旧镜像，docker compose up -d backend 切回即可；DB 无变更，无数据迁移。