## 1. 配置与数据层

- [x] 1.1 `SECTOR_CONFIG_DEFAULTS` 新增 `regime_carrier_enabled`(bool, false) 与 `hold_bear_pct_threshold`(number, 0.2)，sort_order 20-21，含中文 label/description
- [x] 1.2 `get_sector_config`/`list_sector_config` 读取新配置项，旧配置项行为不变；seed 进生产 `golden_pit_sector_config` 表

## 2. 服务层：载体解析与选筹扩展

- [x] 2.1 新增 `resolve_carrier(fund_code, cfg, tech_status) -> {mode, codes, reason}`：auto 按 `trend_up_count >= regime_trend_threshold` 解析，显式 regime 直接映射（oversold→sector_selection / trend→fixed_combo / bh→broad），数据源失败按 sector_selection 兜底
- [x] 2.2 `resolve_regime_mode` 保持兼容并返回解析依据；`_carrier_active` 保留但 `_build_buy_legs` 切换为只消费 `resolve_carrier`
- [x] 2.3 熊市保护：`select_sectors` 在 `hold_until_exit=true` 且 regime=oversold 且宽基贪婪 250 日分位 ≤ `hold_bear_pct_threshold` 时保留持仓、新候选=0（复用 `golden_pit_tech_status._percentile`，数据缺失跳过保护）

## 3. DCA 服务层：统一载体 + 三级 fallback + 软切换

- [x] 3.1 `_build_buy_legs` 消费 `resolve_carrier`：oversold→select_sectors（含 hold_until_exit/fallback）；trend→fixed_combo 静态腿（codes 缺失回退 broad）；bh→broad 腿
- [x] 3.2 三级 fallback 链：sector_selection 选筹空 → fixed_combo 腿 → broad 腿 → 均不可用跳过；摘要标注回退层级
- [x] 3.3 `_sector_fallback_state` 扩展为 `_carrier_state`（记录上次载体）；相邻交易日载体不同 → 摘要标注"载体切换：X → Y（依据）"；切换日不清仓
- [x] 3.4 摘要展示执行载体（`mode=trend（趋势腿激活 N/9）→ 载体 fixed_combo（588200×0.5+512480×0.5）`），保持现有摘要格式兼容

## 4. API 与前端

- [x] 4.1 `/golden-pit/status` 的 `sector_selection.carrier` 扩展为 `{enabled, regime_mode, resolved_mode, resolved_carrier, reason, targets}`
- [x] 4.2 `GoldenPitPage.tsx` 牛熊面板"生效选筹模式"升级为"生效模式 → 执行载体"展示
- [x] 4.3 配置弹窗自动渲染 `regime_carrier_enabled`/`hold_bear_pct_threshold`，前端 `npm run build` 编译 dist

## 5. 验证

- [x] 5.1 单测：`resolve_carrier` auto 阈值边界/显式映射/兜底；三级 fallback 各级触发；熊市保护（分位≤阈值暂停新增）；软切换标注
- [x] 5.2 回归：`regime_carrier_enabled=false` 时输出与 5.4 完全一致（fixed_combo 静态优先，三新功能不参与执行）
- [x] 5.3 dry-run：`/golden-pit/status` 展示 resolved_mode/resolved_carrier 与映射一致；配置持久化 PUT→读回→重启保持
- [x] 5.4 回测脚本（`data/backtest/_rotation_*.py`）补充 regime→carrier 映射变体（2020-2025 分年），验证 trend→fixed_combo 与 oversold→sector_selection 组合是否优于单一载体
