## Context

生产黄金坑板块拆分（588000/159915 仅择时指导，坑内资金按板块 ETF 组合配置）自 add-sector-greed-selection 起使用 `SECTOR_ETF_POOL`（10 板块）与 arkvol `funds-greed/fund` 贪婪，`signal_mode=greed`。现状问题：计算机/软件贪婪停更于 2026-07-09、多只场外代表基金滞后 1 个交易日，有效信号常不足 `min_valid=4` 而空仓；且按生产 500 天分位口径回测，arkvol `tech-hardware-greed/series` 覆盖的 7 只场内科技 ETF 池在 2025 年以来 5 个板块窗口全部跑赢宽基（超额 +5.86%、5/5），优于生产池（+3.21%、3/5）。本次将生产默认选筹池切换为 tech7。

## Goals / Non-Goals

**Goals:**
- 生产板块选筹默认池切换为 tech7（7 只场内科技 ETF），贪婪数据源切换为 `tech-hardware-greed/series`
- 保留 greed 选筹、TOP N、min_valid 空仓、回退宽基/回退后切回、出场规则全部机制不变
- 提供 `pool_source` 配置（tech7 / prod10）以支持快速回滚

**Non-Goals:**
- 不改动宽基出入场与分位逻辑（P70/P80、兜底 20/25 天、500 天滚动分位）
- 不修复 funds-greed 对已停更标的的源端数据（属于外部数据源问题）
- 不新增卖出/风控类技术指标叠加（另行变更）
- 不调整前端选筹展示整体交互，仅按数据源更新文案

## Decisions

**D1: 池来源配置项 `pool_source`（默认 `tech7`）**
- 新增 `golden_pit_sector_config` 配置项 `pool_source`，取值 `tech7` / `prod10`，DB 动态覆盖、代码常量默认 `tech7`
- 理由：避免改错后无法快速恢复；`signal_mode` 已证明 DB 配置热切换可行（60s 缓存）
- 备选：直接改 `SECTOR_ETF_POOL` 并删除旧池 → 回滚需 git revert + 重启，不满足快速回滚目标

**D2: 新池定义 `TECH_SECTOR_POOL`，与 `SECTOR_ETF_POOL` 并存**
- 新增代码常量 `TECH_SECTOR_POOL`（7 只，含 name/etf_code/greed 标记），选筹入口按 `pool_source` 选择池；`SECTOR_ETF_POOL` 保留供 `prod10` 回滚与 moneyflow 模式使用
- 重叠标的（512480/515880/588200）在 tech7 下直接用 tech-hardware 贪婪（与回测口径一致），避免双源混用导致的跨源排序失真

**D3: 贪婪加载双源**
- tech7 池：`ArkvolService.fetch_tech_greed(days=2000)`（`tech-hardware-greed/series`），返回 `data[6位代码] = [{date, greed}]`，缓存 TTL 7200s
- prod10 池：保留 `_load_sector_greed_map`（`funds-greed/fund`）不动
- 理由：两接口返回结构不同（funds-greed 为单基金列表、tech-hardware 为按代码的字典），独立函数更清晰；停更标的的降级行为沿用既有"无值即剔除"

**D4: 超跌与权重沿用现有实现**
- `oversold120` 用 `_fetch_etf_kline`（tushare `fund_daily`，函数名 `_fetch_pi_server_kline` 为历史命名）；权重归一化 + `max_weight` 截断逻辑不变
- 已知差异：生产与回测 K 线同为 tushare `fund_daily`，但生产 `_fetch_pi_server_kline` 不按 `as_of` 截断（固定截至当前日期）：实时运行与回测一致，复选历史窗口时含前视数据，超跌排序与回测的历史时点可能不同（沿用既有已知差异，监控即可）

**D5: 数据源停更兜底**
- 若 tech-hardware 未来也停更或登录态失效：单标的无值即剔除（不影响其他）；有效信号 < `min_valid` 时空仓等待；紧急时 `pool_source=prod10` 或 `signal_mode=moneyflow` 双重回滚

## Risks / Trade-offs

- [tech7 样本仅 5 个板块窗口，结论方向性] → 生产灰度观察 1-2 个坑，收益/超额不如预期时切回 `pool_source=prod10`
- [tech-hardware 与 funds-greed 贪婪刻度可能不同，双源切换后跨期可比性下降] → 回测与生产均统一用 tech7 单源；切换点前后不直接比较板块绝对贪婪值，只比较窗口收益
- [588080（科创50ETF）与宽基同源、航天航空属性错配已剔除] → 新池定义中显式排除，文档记录原因
- [配置表 `pool_source` 需前端弹窗支持] → 本期先支持 DB/API 直改 + 代码默认值，前端弹窗可后续补充（非阻塞）
- [重启生效] → 池常量与配置默认值 import 时加载，变更后需重启 backend（与既有 golden_pit 配置行为一致）

## Migration Plan

1. 代码：新增 `TECH_SECTOR_POOL`、`POOL_SOURCE` 默认 `tech7`、选筹按 pool_source 分池、`fetch_tech_greed` 接入 `_load_sector_greed_map`（或独立加载）
2. 配置：`golden_pit_sector_config` 插入/更新 `pool_source=tech7`
3. 重启 backend，验证 `golden-pit/status` 的 `sector_selection` 使用 tech7 池
4. 观察 1-2 个坑；异常时配置 `pool_source=prod10` 回滚（无需改代码）
5. 更新 `docs/golden-pit-sector-etf-report.md` 与回测脚本池定义

## Open Questions

- `pool_source` 是否需要在黄金坑页面配置弹窗暴露（本期可不做，配置接口直改即可）
- tech7 剔除的 7 个旧板块是否从 `SECTOR_ETF_POOL` 中彻底删除，还是仅"默认池不启用"（倾向后者，保留 moneyflow 回滚选项）
- 机械 159886 在 funds-greed 中仅 97 天数据（2026-03 起），prod10 回滚时是否保留（本期不动，沿用现状）
