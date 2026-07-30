## Why

前端存在大量与后端配置重复的硬编码阈值、颜色和标签映射。当后端 CHINA_INDICES 配置变动时（如新增指数、调整阈值），前端渲染会出现错误：错误的状态颜色、不准确的参考线、未翻译的策略名。这导致前后端数据一致性问题，且难以维护。

## What Changes

1. **状态颜色统一** — 后端 STATUS_MAP 通过 API 下发，前端不再自维护颜色字典
2. **贪婪阈值改为每指数动态** — PortfolioPage 停止使用固定 0.35/0.40 判断恐慌/预警，改用后端返回的 `pit_greed`/`entry_greed`；GoldenPitPage 图表参考线标注为"参考线"而非固定值
3. **DCA 策略名完整翻译** — 覆盖全部 9 种策略的中文标签，后端 `_strategy_weights` 附近新增翻译映射
4. **基金代码列表动态获取** — ChatContainer 工具描述中的基金代码列表移除硬编码，或从实际配置动态生成
5. **后端新增配置端点** — 新增 `GET /golden-pit/display-config` 返回 status_colors、strategy_labels、threshold_labels 等前端展示所需的元数据

## Capabilities

### New Capabilities
- `frontend-display-config`: 后端提供统一的前端展示配置端点，包含颜色、标签、阈值等元数据

### Modified Capabilities
- `golden-pit-exit`: 退出标签通过 display-config 端点统一返回

## Impact

- **后端**: `golden_pit_service.py` 新增 `_strategy_label()` 翻译函数和 `/display-config` 端点
- **前端**: `GoldenPitPage.tsx`, `PortfolioPage.tsx`, `ChatContainer.tsx` 移除硬编码值，改为 API 调用或 props 传递
