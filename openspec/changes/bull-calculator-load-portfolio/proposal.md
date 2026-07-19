## Why

牛股计算器每次都要手动输入代码才能填充持仓数据。后端 `/api/v1/portfolio/positions` 已返回完整的持仓信息（成本价、HWM、现价），一键加载可省去重复录入。

## What Changes

- 牛股计算器新增「加载持仓」按钮，调用 `GET /api/v1/portfolio/positions` 获取服务器持仓
- 自动映射：`avg_price` → 成本价，`high_water_mark` → 阶段顶部，`current_price` → 最新价
- 已在列表中的标的仅更新价格，不重复追加
- 不涉及后端修改

## Capabilities

### New Capabilities

- `bull-calc-portfolio-sync`: 牛股计算器从服务器 API 一键加载持仓数据

## Impact

- `牛股计算器.html`（独立 Vue 3 前端文件，非 React 项目的一部分）
- 依赖 `GET /api/v1/portfolio/positions`（已有，无需修改）
- CORS 已配置 `*`，无需额外处理
