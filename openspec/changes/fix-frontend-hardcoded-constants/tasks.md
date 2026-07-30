## 1. 后端 — 显示配置端点

- [x] 1.1 在 `golden_pit_service.py` 中新增 `_strategy_label()` 翻译函数，覆盖全部 9 种策略
- [x] 1.2 在 `golden_pit_service.py` 中新增 `_display_config()` 函数，返回 status_colors / strategy_labels / status_labels
- [x] 1.3 在 `golden_pit.py` 中新增 `GET /golden-pit/display-config` 端点
- [x] 1.4 在 `_build_index_info()` 和 DCA status 中返回策略中文名 (`dca_label`)

## 2. 前端 — 状态颜色统一

- [x] 2.1 `GoldenPitPage.tsx`: 移除本地 `STATUS_COLORS` 字典，改用 display-config API 返回的颜色
- [x] 2.2 `GoldenPitPage.tsx`: 新增 `useDisplayConfig()` hook，页面加载时缓存配置

## 3. 前端 — DCA 策略名完整翻译

- [x] 3.1 `GoldenPitPage.tsx`: `dca_strategy` 展示改为使用后端返回的 `dca_label` 或 display-config 映射
- [x] 3.2 `ChatContainer.tsx`: DCA 策略描述更新，包含全部 9 种策略

## 4. 前端 — 图表参考线修复

- [x] 4.1 `GoldenPitPage.tsx`: 参考线标签改为 "参考线 (0.35)" / "参考线 (0.40)"
- [ ] 4.2 可选：图表增加每指数 `pit_greed` 水平虚线（仅在单指数视图下）

## 5. 前端 — PortfolioPage 贪婪阈值改为每指数

- [x] 5.1 `PortfolioPage.tsx`: 移除固定 `panicLine=0.35` / `safeCeil=0.50`
- [x] 5.2 `PortfolioPage.tsx`: 从 API 数据的 `indices[n].pit_greed` / `indices[n].entry_greed` 取最小阈值作为参考
- [x] 5.3 `PortfolioPage.tsx`: 更新恐慌条标注，显示 "各指数阈值: pit≤X, warn≤Y"

## 6. 验证

- [ ] 6.1 后端 `display-config` 端点返回数据验证
- [x] 6.2 前端 TypeScript 编译通过
- [ ] 6.3 前端构建 + 推送
