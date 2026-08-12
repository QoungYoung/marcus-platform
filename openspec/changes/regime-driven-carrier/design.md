## Context

生产黄金坑 DCA 当前链路：`_build_buy_legs` → `_carrier_active`（`dca_carrier_enabled=true` + `fixed_combo`/`broad` 静态优先）→ 否则 guide_only 板块选筹（`sector_selection`，含已落地的 hold_until_exit / fallback_broad / regime_mode 选筹）。

问题：载体与 regime 两层脱节——
1. 生产 `dca_carrier_enabled=true` + fixed_combo（588000→588200+512480 等权、159915→159949），`sector_selection` 分支被跳过，三个新功能完全不参与执行（用户已开 `hold_until_exit=true`/`fallback_broad=true`/`regime_mode=auto` 但白开）。
2. `regime_mode=auto` 只切换 `select_sectors` 的选筹风格（oversold/trend），与"实际买什么"（载体）无关；fixed_combo 载体下趋势切换无意义。
3. 回测结论相互印证但未统一：超跌选筹牛市跑输（2024 -8% vs BH +18%、2025 +25.5% vs +71%），高弹性载体坑内跑赢宽基（科创芯片 +19.23%、创业板50 +16.41% vs 宽基 +11.84~14.85%），只截新入牛市占优（2025 +63% vs +25.5%）、熊市拖累（2022 -21.3% vs -1.2%）。

## Goals / Non-Goals

**Goals:**
- 建立"环境（regime）→ 载体（carrier）"统一决策链：`oversold`→sector_selection、`trend`→fixed_combo、`bh`→broad，`_build_buy_legs` 只消费 `resolve_carrier` 结果
- 保留 5.4 fixed_combo 在主升/趋势环境的高弹性收益，同时让超跌环境恢复动态选筹（含新三件套）
- fallback 升级为三级链（sector_selection 空 → fixed_combo → broad）
- 熊市保护 hold_until_exit（oversold + 宽基贪婪分位低位时暂停新增候选）
- 载体切换软交接（不清仓、旧持仓按既有退出规则离场）
- 前端牛熊面板展示执行载体

**Non-Goals:**
- 全球资产波段（纳斯达克/道琼斯 + 硬科技轮动）独立系统
- 贪婪止盈冷却参数（回测判定过拟合）
- 宽基出入场规则修改（P 分位出场/兜底天数/连跌天数）
- 低频率轮动（每 N 天动量再平衡）——需回测验证后另立变更

## Decisions

**D1. `resolve_carrier(fund_code, cfg, tech_status) -> {mode, codes, reason}` 统一入口**
- 替代 `_carrier_active` 的静态优先级：`_build_buy_legs` 只消费解析结果。
- auto：`resolve_regime_mode` 解析 regime → 映射载体；显式 regime 直接映射。
- 数据源失败按 `sector_selection` 兜底（与 resolve_regime_mode 兜底一致）。
- 备选：保留 `_carrier_active` 再加映射——否决，双层判断是脱节根源。

**D2. regime → 载体映射表**
- `oversold`→`sector_selection`（动态选筹：greed 超跌 + hold_until_exit + 三级 fallback）
- `trend`→`fixed_combo`（复用 `dca_carrier_<fund>` codes；codes 缺失回退 broad）
- `bh`→`broad`（宽基躺平）
- `select_sectors(mode=trend)` 动量选筹保留为展示/备用路径（`regime_carrier_enabled=false` 且 `regime_mode=trend` 时仍可执行）。

**D3. 三级 fallback 链（仅 sector_selection 载体路径）**
- 选筹空（`selected=[]`）→ 第一级 fixed_combo 腿 → 第二级 broad 腿 → 两级均不可用跳过。
- 沿用 `_sector_fallback_state` 记录上次回退，信号恢复时标注"切回板块选筹"。
- 备选：维持两级（空→宽基）——否决，回测显示固定高弹性组合坑内收益优于宽基，回退应先高弹性后宽基。

**D4. 熊市保护 hold_until_exit**
- `hold_until_exit=true` + regime=oversold + 宽基贪婪 250 日分位 ≤ `hold_bear_pct_threshold`（默认 0.2）→ 保留持仓、新候选=0。
- 宽基贪婪分位复用 `golden_pit_tech_status._percentile`（DB 快照/arkvol）；数据缺失跳过保护（保持原行为）。

**D5. 软切换与状态**
- `_sector_fallback_state` 扩展为 `_carrier_state: {fund_code: {"carrier", "as_of"}}`；相邻交易日载体不同 → 摘要标注"载体切换：X → Y（依据）"。
- 切换日不触发清仓；旧持仓由板块连跌/宽基退出规则离场。

**D6. 配置默认保守**
- 新增 `regime_carrier_enabled`（bool，默认 false）——关闭时完全保持 5.4 行为（fixed_combo 静态优先）。
- 新增 `hold_bear_pct_threshold`（number，默认 0.2）。
- `dca_carrier_<fund>` 保留为 trend 载体定义；`dca_carrier_enabled` 在 `regime_carrier_enabled=true` 时不再决定优先级（兼容读法保留）。

**D7. 前端展示**
- `/golden-pit/status` 的 `sector_selection.carrier` 扩展为 `{enabled, regime_mode, resolved_mode, resolved_carrier, reason, targets}`。
- 牛熊面板"生效选筹模式"升级为"生效模式 → 执行载体"；配置弹窗自动渲染 `regime_carrier_enabled`/`hold_bear_pct_threshold`。

## Risks / Trade-offs

- **[trend→fixed_combo 在震荡市追高]** → trend 由 `trend_up_count >= 5` 确认（MA20+斜率，偏滞后），阈值可配；先 dry-run 观察再灰度执行。
- **[软切换使旧板块持仓与 fixed_combo 并存]** → 并存期短暂且受板块连跌/宽基退出约束；日志全程标注载体与切换。
- **[三级 fallback 增加高弹性暴露]** → fallback 仅在选筹空（min_valid 不足）时触发，坑内 fixed_combo 历史收益为正；`fallback_broad=false` 可关闭整条链。
- **[熊市保护依赖贪婪分位数据]** → 数据缺失跳过保护（保持原 hold_until_exit），不引入新故障点。
- **[回测口径差异]** → 生产 DCA 分批买入 vs 回测集中进出场：参数先 dry-run，逐窗口观察后再开执行。

## Migration Plan

1. 灰度 `regime_carrier_enabled=false` 上线（行为与 5.4 完全一致，仅新增配置项与展示字段）。
2. dry-run 观察：页面"执行载体"显示 resolved_mode/resolved_carrier，确认映射符合预期。
3. 回测验证 regime→carrier 映射（`data/backtest/_rotation_*.py` 补充 2020-2025 分年变体）后再灰度 `true`。
4. 回滚：置 `regime_carrier_enabled=false` 即恢复 5.4 静态载体；无需迁移数据。

## Open Questions

- trend 环境下 fixed_combo（静态高弹性）与"动量选筹"（T-TOP4 只截新入）哪个更优——需回测分年对比后定默认。
- `hold_bear_pct_threshold=0.2` 是否合适——回测熊市窗口（2022/2024）验证。
