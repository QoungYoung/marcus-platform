## Why

2026-08-26 生产事故：05:17:12–05:23:21 约 6 分钟内，全部 /api/v1/* 接口超时（TCP 可连接但 0 字节返回），backend 容器被 Docker 标记 unhealthy。根因是 `/api/v1/golden-pit/status` 这个 async 接口在 uvicorn 单进程事件循环上**同步**执行整套黄金坑状态计算：DB 快照历史不足 60 天触发「回退 API」慢路径，串行调用 Tushare（单次 30s 超时）与 ArkVol（单次 60s 超时）等外部数据源，事件循环被冻住约 6 分钟，连健康检查都无法响应。外部源恢复后自愈，但缓存过期或重启后仍会复发。

## What Changes

- golden-pit 相关 API（/status、/history、/snapshots、/tech-status）的同步重计算改到线程池执行（run_in_threadpool / executor），事件循环不再被阻塞。
- 外部数据源调用（Tushare / ArkVol / Xueqiu / 腾讯行情）全部加**有界超时**（≤10s/次）与失败降级，杜绝无上限等待；调用链串行叠加量受到约束。
- `/api/v1/golden-pit/status` 增加响应级 TTL 缓存，并在启动/调度任务中预热，冷缓存时也不触发页面级重计算。
- backend 以多 worker 运行（uvicorn `--workers`），单个 worker 繁忙不再拖垮健康检查与其他端点。
- 健康检查 `/api/v1/health` 在重计算期间保持可响应（不被同一事件循环阻塞）。

## Capabilities

### New Capabilities
- `api-responsiveness`: HTTP API 在外部行情数据源缓慢或不可用时仍保持响应且有界延迟；事件循环不执行同步重活；慢 handler 不影响健康检查与其他端点。

### Modified Capabilities
<!-- 无：本变更不改动既有 spec 的需求，只引入新的 API 可用性保证 -->

## Impact

- 代码：`backend/app/api/golden_pit.py`（async 接口解阻塞）、`backend/app/services/golden_pit_service.py`（get_status 慢路径、缓存、超时）、`backend/app/services/arkvol_service.py`（外部调用超时）、`backend/app/core/trading/_api_config.py` 与 `_fetch_pi_server_kline`（Tushare 超时）。
- 部署：docker compose 中 backend 启动命令增加 uvicorn workers；重建 backend 容器。
- 兼容性：无 DB schema 变更，无 API 契约变更（响应结构不变），前端无需改动。
