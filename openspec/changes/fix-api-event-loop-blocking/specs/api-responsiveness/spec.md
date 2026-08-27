## Purpose

保证 Marcus 平台 HTTP API 在外部行情数据源缓慢或不可用时仍保持响应且有界延迟，任何单个慢 handler 都不允许冻结健康检查或其他端点（基于 2026-08-26 golden-pit/status 阻塞事件循环致全站 API 超时 6 分钟的事故）。

## ADDED Requirements

### Requirement: API 在重计算期间保持可响应
系统 SHALL 保证：任意 handler 执行长耗时计算（如黄金坑状态重算）期间，健康检查端点与其他不相关的 API 端点仍能在有界时间内返回响应，不得出现「TCP 可连接但长时间 0 字节」的挂起。

#### Scenario: 黄金坑重算进行中健康检查仍可用
- **WHEN** /api/v1/golden-pit/status 正在执行慢速外部数据获取（DB 快照不足回退 API 路径）
- **THEN** /api/v1/health 在 1 秒内返回 HTTP 200

#### Scenario: 单请求缓慢不拖垮其他请求
- **WHEN** 一个请求正被慢速外部调用阻塞
- **THEN** 其他并发请求仍能在有界时间内完成，或快速失败返回明确错误（5xx），而不会无限期挂起

### Requirement: 黄金坑状态接口有界延迟
/api/v1/golden-pit/status SHALL 在冷缓存且外部数据源缓慢的场景下仍于 5 秒内返回响应（可返回缓存/过期数据或明确的降级结果），不得因外部依赖而无上限等待。

#### Scenario: 冷缓存 + 外部 API 缓慢
- **WHEN** 缓存为空且 Tushare/ArkVol 等外部 API 响应缓慢
- **THEN** /api/v1/golden-pit/status 在 5 秒内返回（可携带降级/过期数据或明确的错误信息）

#### Scenario: 缓存热时重复请求
- **WHEN** 同一计算结果的缓存仍在 TTL 有效期内
- **THEN** /api/v1/golden-pit/status 直接返回缓存结果，且延迟小于 1 秒

### Requirement: 外部数据源调用有界超时
系统对全部外部行情/贪婪数据源调用（Tushare、ArkVol、Xueqiu、腾讯行情）SHALL 设置单次有界超时（不超过 10 秒），超时或失败后 SHALL 优雅降级（回退缓存/过期数据，或返回空结果并标记错误），不得无限等待或无限重试。

#### Scenario: 外部调用超时
- **WHEN** 某外部数据源在超时时间内未返回
- **THEN** 对应 handler 在总超时预算内返回降级结果或明确错误，进程不被阻塞

#### Scenario: 外部调用失败串联不叠加成分钟级
- **WHEN** 多个外部调用在单次请求中连续失败
- **THEN** 总耗时受有界超时与调用数量上限约束，单请求总等待不超过 10 秒（降级路径）

### Requirement: 健康检查可靠性
/api/v1/health SHALL 在后台或请求触发的重计算期间保持有界响应（可报告降级状态，但不得超时），Docker 健康检查不得因业务 handler 繁忙而判定容器不健康。

#### Scenario: 重计算期间健康探测
- **WHEN** 容器内健康探测命中 /api/v1/health 且同时存在重计算
- **THEN** 探测在 1 秒内收到 HTTP 200 响应

### Requirement: 并发隔离
API 服务 SHALL 以至少 2 个 worker（或等效隔离机制）运行，使单个 worker 繁忙时不阻止服务接受并响应请求。

#### Scenario: 单 worker 繁忙仍可服务
- **WHEN** 一个 worker 正忙于慢请求
- **THEN** 新请求由其他 worker 正常处理，连接不被积压挂起
