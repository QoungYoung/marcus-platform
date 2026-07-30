## 1. 后端 — 扩展 display-config 端点

- [x] 1.1 `golden_pit_service.py`: 在 `_display_config()` 中新增 `exit_labels` 映射（half_exit/full_exit/stop_profit/fallback_exit）
- [x] 1.2 `golden_pit_service.py`: 在 `_display_config()` 中新增 `trend_icons` 映射（declining/bottoming/recovering）
- [x] 1.3 `golden_pit_service.py`: 在 `_display_config()` 中新增 `trend_colors` 映射（declining/bottoming/recovering）

## 2. 前端 — DisplayConfig 接口更新

- [x] 2.1 `GoldenPitPage.tsx`: 在 `DisplayConfig` 接口中新增 `exit_labels`、`trend_icons`、`trend_colors` 字段
- [x] 2.2 `GoldenPitPage.tsx`: 更新 `fetchDisplayConfig()` fallback 默认值包含新字段
- [x] 2.3 `GoldenPitPage.tsx`: 移除本地 `EXIT_LABELS`、`TREND_ICONS`、`TREND_COLORS` 常量

## 3. 前端 — 退出/趋势标签改用 display-config

- [x] 3.1 `GoldenPitPage.tsx`: `IndexStatusCard` — 退出信号标签改用 `displayConfig.exit_labels`
- [x] 3.2 `GoldenPitPage.tsx`: `IndexStatusCard` — 趋势图标改用 `displayConfig.trend_icons`
- [x] 3.3 `GoldenPitPage.tsx`: `IndexStatusCard` — 趋势颜色改用 `displayConfig.trend_colors`

## 4. 前端 — 图表动态阈值

- [x] 4.1 `GoldenPitPage.tsx`: `TrendChart` — YAxis domain 改为动态计算（基于数据范围和 per-index 阈值）
- [x] 4.2 `GoldenPitPage.tsx`: `TrendChart` — ReferenceLine y-位置改为从 indices 数据中取 min `pit_greed` / `entry_greed`

## 5. 前端 — 共振乘数 & 仪表盘公式

- [x] 5.1 `GoldenPitPage.tsx`: `ResonanceBadge` — 移除本地 fallback 乘数计算公式，multiplier 缺失时不显示
- [x] 5.2 `PortfolioPage.tsx`: 替换 `safeCeil = max(warnLine + 0.10, 0.50)` 为 `max(maxEntryGreed + 0.10, 0.50)`

## 6. 验证

- [x] 6.1 前端 TypeScript 编译通过
- [ ] 6.2 前端构建 + 推送
