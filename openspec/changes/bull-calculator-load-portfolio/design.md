## Context

牛股计算器是独立 HTML 文件（Vue 3 + Element Plus CDN），通过 JSONP 调用 `qt.gtimg.cn` 获取行情。后端 `GET /api/v1/portfolio/positions` 返回 `list[PositionResponse]`，包含：`symbol`, `name`, `avg_price`, `current_price`, `high_water_mark` 等字段。

## Goals / Non-Goals

**Goals:**
- 新增按钮一键从服务器加载持仓到计算器
- 已存在的标的跳过追加，仅刷新行情
- 阶段顶部自动填充 HWM，阶段底部留空由用户手动调整

**Non-Goals:**
- 不修改后端 API
- 不自动同步阶段底部（需要 90 日 K 线数据，超出范围）

## Decisions

### 字段映射

| 服务器字段 | 计算器字段 | 说明 |
|-----------|-----------|------|
| `symbol` | `fullCode` | 需转换格式：`000725` → `sz000725`（复用已有 `formatCode`） |
| `name` | `name` | 直接映射 |
| `current_price` | `now` | 加载后自动刷新腾讯行情覆盖 |
| `avg_price` | `cost` | 成本价 |
| `high_water_mark` | `high` | 持仓期最高价，fallback 为 `current_price` |
| (无) | `low` | 不自动填充，留空由用户手动调整 |

### 去重策略

以 `fullCode` 为 key，已在 `tableData` 中的标的只刷新行情不追加。仅在不存在时追加新行。

### API 调用

使用 `fetch`（非 JSONP）+ CORS。后端已配置 `allow_origins=["*"]`。调用 `/api/v1/portfolio/positions`，返回值 `PositionResponse[]`。

## Risks / Trade-offs

- **HWM 可能为 null**：新开仓无 HWM 时 fallback 到 `current_price`
- **低值手动调整**：阶段底部是斐波那契计算的关键参数，HWM 没有提供阶段底部，用户需自行填写
