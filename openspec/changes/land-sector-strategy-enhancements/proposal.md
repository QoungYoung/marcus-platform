## Why

回测（`data/backtest/_rotation_*`、`_greed_quantile_band`）与生产复盘暴露出三个"已验证但未落地"的策略结论：

1. **只截新入更优**：板块轮动回测中，"持仓保留到被动量/退出规则挤掉"（cutoff=new）相比"每日全量重排"（cutoff=all）在牛市显著占优（2025 年 +63.0% vs +25.5%），且两窗口互相印证。生产 `select_sectors` 目前是每日全量重排语义——持仓板块一旦跌出 TOP N 当日即停止买入，即使未触发板块退出规则（连 N 日回落/坑结束）。
2. **选筹失败应回退宽基**：回测验证"坑后选筹失败回退宽基 + 拐点后切回板块"的混合模式有效；生产目前选筹为空时直接"跳过当日买入"（`_build_buy_legs` 返回 `empty_reason`，调用方 `golden_pit_dca_service.py:1417` 跳过），既无回退也无切回。
3. **牛市缺少进攻模式**：回测反复证明超跌（贪婪）选筹在牛市跑输躺平/趋势（2024/2025 等权 BH +18%/+71% vs 策略 -8%/+25%）；生产只有"熊市防御承接"（已落地），没有"牛熊状态驱动选筹模式切换"（趋势腿激活→动量选筹/躺平；超跌腿→现 greed 选筹）。

## What Changes

- **只截新入（hold-until-exit）**：`select_sectors` 支持传入当前板块持仓（`holdings`），新增配置 `hold_until_exit`（bool）；开启后已持仓板块在未触发退出规则前保留在目标组合中，仅对新进入候选做 TOP N 截断；DCA 执行时把当前板块模拟持仓传入选筹。
- **选筹失败回退宽基（fallback-broad）**：新增配置 `fallback_broad`（bool）；`_build_buy_legs` 在板块选筹为空且宽基在坑内时，改为买入宽基本身 ETF（不再跳过），板块信号恢复后自然切回板块（与回测混合模式口径一致）。
- **牛熊状态驱动的选筹模式（bull-regime）**：新增配置 `regime_mode`（`auto`/`oversold`/`trend`/`bh`）与 `regime_trend_threshold`；`auto` 模式读取 `golden-pit/tech-status`（趋势腿激活数 trend_up_count），达到阈值时切换到"20 日动量 TOP N + 只截新入"的趋势选筹，否则维持现 greed 超跌选筹；`bh` 直接买宽基本身（牛市躺平选项）。
- **前端**：黄金坑页面"牛熊判断 · 科技现状"面板展示当前 regime_mode 生效模式与趋势腿激活状态；配置弹窗新增上述配置项（sector-config 自动渲染）。

## Capabilities

### New Capabilities
- `sector-hold-until-exit`: 板块选筹持仓保留（只截新入）语义，持仓板块不被每日重排替换，直到退出规则触发
- `sector-fallback-mixed-mode`: 选筹失败回退宽基 + 板块信号恢复后切回板块的混合模式
- `bull-regime-selection`: 牛熊状态驱动的选筹模式切换（超跌/趋势/躺平）

### Modified Capabilities
<!-- 主 specs 中无 golden-pit-sector-etf-split 能力（板块拆分能力目前仅存在于各变更的 delta spec），本次三个新能力均以新 capability 承接 -->

## Impact

- `backend/app/services/golden_pit_sector_service.py`：`select_sectors` 增加 `holdings`/`mode` 参数与动量选筹分支；`SECTOR_CONFIG_DEFAULTS` 新增 `hold_until_exit`、`fallback_broad`、`regime_mode`、`regime_trend_threshold`
- `backend/app/services/golden_pit_dca_service.py`：`_build_buy_legs` 传入持仓并处理 `fallback_broad` 回退；买入摘要展示生效模式
- `backend/app/services/golden_pit_tech_status.py`：`get_tech_status` 输出保持兼容（供 `auto` 模式读取 trend_up_count）
- `golden_pit_sector_config`（PostgreSQL）：新增 4 个配置项，前端配置弹窗自动渲染
- `frontend/src/pages/GoldenPitPage.tsx` / `golden-pit-page.css`：牛熊面板展示生效选筹模式
- 回测脚本（`data/backtest/_rotation_*.py`）与文档：结论引用保持一致
- 不涉及：全球资产波段（纳斯达克/道琼斯）独立系统、贪婪止盈冷却参数（已判定过拟合不采纳）
