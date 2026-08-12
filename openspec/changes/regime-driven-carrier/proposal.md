## Why

载体（坑内买入对象）与 regime（牛熊状态）是两层脱节的开关：当前 `fixed_combo`（固定高弹性）与 `sector_selection`（动态选筹）只能二选一，新落地的三个功能（hold_until_exit/fallback_broad/regime 选筹）只活在 sector_selection 分支里——生产开着 fixed_combo 时全部不生效。回测反复证明：超跌选筹在牛市跑输躺平/固定高弹性（2024 -8% vs BH +18%，2025 +25.5% vs +71%），而高弹性载体在坑内跑赢宽基（科创芯片 +19.23% / 宽基 +11.84%）。需要一个统一决策链：**环境（regime）决定载体，载体决定买什么**。

## What Changes

- **regime 决定载体（regime-driven-carrier）**：新增配置 `regime_carrier_enabled`（bool，默认 false 保持现状）；开启后 DCA 按解析出的 regime 自动选择执行载体——`oversold`→`sector_selection`（超跌贪婪选筹）、`trend`→`fixed_combo`（复用 `dca_carrier_<fund>` 高弹性组合）、`bh`→`broad`（宽基躺平）。载体不再二选一，而是各司其职。
- **载体解析统一入口**：`resolve_carrier(fund_code, cfg, tech_status)` 返回 `{mode, codes, reason}`；auto 按趋势腿激活数 `trend_up_count >= regime_trend_threshold` 切 trend，数据源失败按 `sector_selection` 兜底；`_build_buy_legs` 只消费解析结果，删除 `_carrier_active` 的静态优先级。
- **fallback 升级为三级链**：`sector_selection` 选筹为空 → 回退 `fixed_combo` 静态腿 → 再回退 `broad` 宽基腿；摘要标注回退层级（区别于现状"空→宽基"两级）。
- **熊市保护 hold_until_exit**：regime=`oversold` 且宽基贪婪分位处于低位时，保留持仓但暂停新增候选（防 2022 年只截新入 -21.3% 的拖累）。
- **持仓软切换**：regime/载体切换日不强制清仓——旧持仓按既有退出规则离场，新增资金按新载体买入，日志标注"载体切换"。
- **前端**：牛熊面板展示"执行载体"（sector_selection/fixed_combo/broad + 依据），配置弹窗自动渲染 `regime_carrier_enabled`。

## Capabilities

### New Capabilities
- `regime-driven-carrier`: 环境（regime）→ 载体（carrier）映射决策层：auto 时按科技趋势腿激活数解析执行载体，作为 DCA 买入腿的唯一来源

### Modified Capabilities
- `bull-regime-selection`: regime 语义从"选筹风格切换"升级为"载体切换"（auto 解析结果直接决定执行载体，oversold/trend/bh 分别映射 sector_selection/fixed_combo/broad）
- `sector-hold-until-exit`: 增加熊市保护（oversold 且贪婪分位低位时保留持仓、暂停新增候选）与载体切换时的持仓软交接
- `sector-fallback-mixed-mode`: 回退从"选筹空→宽基"扩展为"选筹空→fixed_combo→宽基"三级链，标注回退层级

## Impact

- `backend/app/services/golden_pit_dca_service.py`：`_build_buy_legs` 改为消费 `resolve_carrier` 结果；新增三级 fallback 与软切换状态；摘要标注执行载体/回退层级/载体切换
- `backend/app/services/golden_pit_sector_service.py`：`resolve_regime_mode` 扩展（新增贪婪分位信号）；`SECTOR_CONFIG_DEFAULTS` 新增 `regime_carrier_enabled` 及熊市保护参数
- `backend/app/services/golden_pit_tech_status.py`：输出保持兼容（复用 trend_up_count / 贪婪分位）
- `golden_pit_config.py`：`DCA_CARRIER_DEFAULTS` 保留，作为 trend 模式的高弹性组合定义
- `frontend/src/pages/GoldenPitPage.tsx`：牛熊面板展示执行载体；配置弹窗自动渲染新项
- 回测脚本（`data/backtest/_rotation_*.py`、`_greed_quantile_band.py`）：补充 regime→carrier 映射回测变体
- 不涉及：全球资产波段独立系统、贪婪止盈冷却参数（过拟合不采纳）、宽基出入场规则修改
