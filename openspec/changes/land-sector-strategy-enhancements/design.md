## Context

生产黄金坑板块拆分（guide_only 宽基：科创50/创业板只做择时，坑内资金按板块 ETF 组合买入）当前链路：
`golden_pit_dca_service._build_buy_legs` → `golden_pit_sector_service.select_sectors`（greed 模式：超跌 `oversold120<0` + 贪婪升序 combo，TOP N 归一化权重）→ 空仓跳过买入；退出由宽基窗口（P70/P80/P40/兜底 20/25 天）与板块连 `exit_down_days` 日回落驱动。

回测结论（`data/backtest/_rotation_*`、`_greed_quantile_band`）与生产现状存在三处差距：
1. 选筹每日全量重排（等价 cutoff=all），持仓板块跌出 TOP N 即停止买入 → 频繁换手、牛市踏空；回测"只截新入"（cutoff=new）两窗口占优。
2. 选筹为空直接跳过当日买入 → 无"回退宽基 + 切回板块"的混合模式。
3. 无牛熊状态驱动的选筹模式切换 → 牛市里超跌选筹跑输躺平/动量。

已具备的基础：`golden-pit/tech-status` 接口输出趋势腿激活数（trend_up_count，MA20+斜率口径）；`golden_pit_sector_config` 配置表 + 前端配置弹窗自动渲染；DCA 板块模拟持仓查询 `_get_sector_holdings`。

## Goals / Non-Goals

**Goals:**
- 落地"只截新入"持仓保留语义（可配置 `hold_until_exit`，默认关闭以保持现状，开启后对齐回测变体）
- 落地"选筹失败回退宽基 + 信号恢复切回板块"混合模式（可配置 `fallback_broad`，默认关闭）
- 落地牛熊状态驱动的选筹模式（`regime_mode`：auto/oversold/trend/bh，默认 `oversold` 保持现状），auto 模式基于 tech-status 趋势腿激活数切换
- 前端牛熊面板与配置弹窗展示生效模式

**Non-Goals:**
- 全球资产波段（纳斯达克/道琼斯 + 硬科技贪婪轮动）独立系统——另立变更
- 贪婪止盈冷却参数（回测判定过拟合：冷3/冷5 差 40pp）——明确不落地
- 回测记账 bug 修复——只影响回测框架，生产无此问题
- 修改既有退出规则（P 分位出场、兜底天数、板块连跌天数）

## Decisions

**D1. `select_sectors` 增加 `holdings` 与 `mode` 参数，保持向后兼容**
- `holdings: List[str]`（etf_code 6 位）——`hold_until_exit=true` 时，`selected = 持仓保留 ∪ TOP N 新候选`；新候选按 combo（或动量）排序截断；持仓保留权重参与归一化（保留原权重份额，超出 `max_weight` 按现有截断逻辑处理）。`holdings` 为空或配置关闭时行为不变。
- `mode: str`（`oversold`/`trend`/`bh`）——`trend` 复用超跌计算但改用 20 日动量排序（`close[d]/close[d-20]-1` 降序），不设超跌门槛、不参与贪婪排序；`bh` 由 DCA 层处理（直接买宽基），`select_sectors` 不感知。

**D2. regime 解析放在 DCA/服务层，tech-status 保持纯数据**
- 新增 `resolve_regime_mode(cfg, tech_status) -> (mode, reason)`：`auto` 时 `trend_up_count >= regime_trend_threshold` → `trend`，否则 `oversold`；显式值直接返回。选筹结果与买入摘要带 `regime_mode`/`trend_up_count` 字段。

**D3. `_build_buy_legs` 回退与持仓传递**
- guide_only + 板块拆分启用时：
  1. `regime_mode` 解析（auto 时读 tech-status，缓存 15 分钟）
  2. `holdings = _get_sector_holdings(fund_code)`（仅 `hold_until_exit=true` 时传入，避免依赖）
  3. `select_sectors(as_of, holdings, mode)`；`mode=bh` 时跳过选筹直接返回宽基腿
  4. `selected` 为空且 `fallback_broad=true` → 返回宽基本身 ETF 腿，标注回退原因；否则保持跳过

**D4. 配置默认值保守**
- 新配置项：`hold_until_exit=false`、`fallback_broad=false`、`regime_mode=oversold`、`regime_trend_threshold=5`（均默认维持现状，灰度开启）；`SECTOR_CONFIG_DEFAULTS` 增加 4 项（sort_order 13-16），前端弹窗自动渲染。

**D5. 前端展示**
- 牛熊面板新增"生效模式"行：`regime_mode`（含 auto 解析结果）+ `trend_up_count`；配置弹窗自动出现 4 个新配置项，无需额外改表单代码。

## Risks / Trade-offs

- **只截新入的熊市回撤**：回测 2022 年只截新入 -21.3% vs 全截断 -1.2%（持仓保留在熊市拖累防御）——默认关闭，且退出规则（板块连跌/宽基退出）优先级高于保留，可在熊市验证后收紧。
- **auto 模式切换滞后**：trend_up_count 基于 MA20 确认，牛市启动初期可能滞后 1-2 周——阈值可配置，且 `oversold` 为默认兜底，切换只在"确认偏牛"后发生。
- **回退宽基与 DCA 载体冲突**：`fallback_broad` 仅作用于 sector_selection 载体路径；`fixed_combo`（5.4 灰度）路径不动，避免改变已上线行为。
- **tech-status 依赖**：auto 模式依赖 tushare K线（.env 代理）与 arkvol/DB 贪婪；数据源失败时按 `oversold` 兜底并记录告警。
- **回测口径差异**：生产 DCA 是分批买入（每日一部分），回测是集中进出场——只截新入/回退的参数建议先在 dry-run 观察，再灰度执行。
