## Why

黄金坑窗口内 DCA 执行载体换成高弹性板块 ETF 可系统性放大收益（生产 500 天分位出入场、生产 DCA 形态回测）：科创50 信号 8 窗口下 588200 科创芯片 +19.23%（5/7）、512480 半导体 +18.08%（5/8），宽基对照仅 +11.84%；创业板信号 6 窗口下 159949 创业板50 +16.41%（5/6），宽基对照 +14.85%。当前生产 guide_only 宽基（588000/159915）坑内 DCA 一律按板块选筹（tech7 池 combo TOP2）执行，缺少「固定高弹性执行载体」的可配置选项；本坑（2026-07 创业板坑）选出的 AI+5G 明显弱于半导体/科创芯片，错过高弹性载体的超额。

## What Changes

- 新增每指数 DCA 执行载体配置 `dca_carrier`（DB 动态配置 + 代码常量默认），模式三选一：
  - `sector_selection`：维持现状——按 tech7 板块选筹 combo TOP N 分配坑内资金
  - `fixed_combo`：固定高弹性 ETF 等权/加权组合（科创50 信号推荐 588200+512480；创业板信号推荐 159949），退出沿用宽基窗口退出（full_exit/stop_profit/fallback_exit）
  - `broad`：回退宽基本身直接买入（对照/回滚用）
- 新增灰度开关 `dca_carrier_enabled`（默认 `false`）：关闭时保持现状下单（`sector_selection`）且仅展示目标载体；开启后 `_build_buy_legs` 按载体模式解析买入 legs
- 载体配置落 PostgreSQL（沿用 `golden_pit_sector_config` 表机制或同模式新表），黄金坑页面配置弹窗支持修改（复用现有 sector-config 弹窗交互）
- 信号链路完全不变：入坑/拐点/出场仍以宽基贪婪（500 天滚动分位、P70/P80、兜底 20/25 天）为唯一指导；载体只改变 DCA 买入对象与金额分配
- **BREAKING**（仅灰度开启后）：`fixed_combo` 模式下坑内资金不再按板块选筹结果分配，而是按固定载体权重买入

## Capabilities

### New Capabilities
- `dca-high-beta-carrier`: 定义每指数 DCA 执行载体配置（模式 sector_selection/fixed_combo/broad、载体标的与权重、灰度开关、dry-run 展示与执行切换）

### Modified Capabilities
- `golden-pit-dca-schedule`: DCA 执行对象从「板块选筹结果（唯一路径）」扩展为「按载体配置解析的买入 legs（选筹 / 固定高弹性组合 / 宽基）」

## Impact

- `backend/app/services/golden_pit_config.py`：新增 `DCA_CARRIER_DEFAULTS`（每指数默认载体与权重）与开关常量
- `backend/app/services/golden_pit_dca_service.py`：`_build_buy_legs` 增加载体模式分支；dry-run 报告展示目标载体
- `backend/app/services/golden_pit_sector_service.py`（或配置服务）：载体配置读写与 60s 缓存
- PostgreSQL 配置表：`golden_pit_sector_config` 新增 `dca_carrier`/`dca_carrier_enabled` 项（或同模式新表）
- 前端黄金坑页面：配置弹窗新增 DCA 载体分组（模式下拉 + 标的权重编辑 + 开关）
- 回测资产：`data/backtest/_dca_elastic_hist.py`（已产出 8+6 窗口、当前坑快照）作为参数来源；文档 `docs/golden-pit-sector-etf-report.md` 补充载体对比表
- 非目标：场外基金载体（008984 申购链路）本期不做，仅预留配置结构；跌加因子（暴跌加大定投）另立变更
