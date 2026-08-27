## 1. 事件循环解阻塞（D1）

- [x] 1.1 golden_pit.py：/status、/history、/snapshots、/tech-status 改为普通 def 或 run_in_threadpool 包裹，确认事件循环不再被同步计算阻塞
- [x] 1.2 排查 backend/app/api 下其他 async def 直接调用阻塞服务的地方，同类问题一并修复
- [x] 1.3 golden-pit handler 增加总 deadline（服务层 5s），超时返回降级结果而非无限等待

## 2. 外部调用有界超时（D3）

- [x] 2.1 get_tushare_pro 创建 DataApi 时传 timeout=10，_fetch_pi_server_kline 的 fund_daily/fund_adj 受约束
- [x] 2.2 arkvol_service.py 的 urlopen timeout 由 60 降为 10（配置化），异常沿用现有降级
- [x] 2.3 Xueqiu / 腾讯行情调用（get_stock_quote、get_realtime_prices、行情 helper）增加显式超时 5-10s，失败返回缓存/零值并记日志
- [x] 2.4 约束外部调用重试：单次请求内同一调用最多重试 1 次，且计入总 deadline

## 3. golden-pit 缓存与预热（D2）

- [x] 3.1 GoldenPitService.get_status 增加 TTL 缓存（默认 300s）+ single-flight 锁，并发请求共享一次计算
- [x] 3.2 冷缓存/deadline 场景返回最近成功快照或最小结构，响应带 _source 字段（db|cached|stale|api）
- [x] 3.3 FastAPI lifespan 启动后台线程预热一次 golden-pit 状态
- [x] 3.4 save_daily_snapshot 定时任务落库后刷新该缓存

## 4. 健康检查与并发隔离（D5 + D4）

- [x] 4.1 /api/v1/health 保持轻量纯异步（DB ping + 调度状态），不触碰外部行情 API
- [x] 4.2 检查 app.main lifespan 的 daemon（vnpy bridge、scheduler、4 个 monitor）是否已有进程级守卫；若无则用 WORKER_INDEX=0 或只读锁门控
- [ ] 4.3 docker compose backend 启动命令加 --workers 3，验证 3 worker 下调度/监控无重复执行
- [x] 4.4 未完成 4.2 门控前保持 1 worker（D1-D3 已消除事故根因，多 worker 仅作防御纵深）

## 5. 验证与回归

- [x] 5.1 单测：golden-pit/status 冷缓存 + 外部源缓慢时 ≤5s 返回（mock 外部源）；health 在重计算期间 <1s
- [ ] 5.2 集成验证：portfolio、equity-history、daily-pnl-breakdown、market/indices、golden-pit/status 五个端点缓存冷/热下均 <1s
- [ ] 5.3 生产部署：重建 backend 容器，观察 /health、golden-pit/status 延迟与容器健康状态
- [ ] 5.4 回归：t/*、行情 quote/kline、调度与监控任务无重复执行、无新增告警