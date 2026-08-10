## Context

黄金坑策略当前对科创50（588000）/创业板指（159915）直接买入宽基 ETF，坑内仅有固定 5%+5% 的半导体增强（`SEMI_BOOST_INDICES` 588200/512480，按 ArkVol tech 贪婪信号）。前期回测已证明：坑内板块分化显著（科创50 窗口最强/最弱板块 d30 平均差 +19.9%、创业板 +17.2%），且 combo 信号（超跌 oversold120 + 中信二级 5 日资金流 mf5_norm）落地代表 ETF 可获超额（科创50 +2.19%/胜率 73%，创业板 +5.19%/胜率 64%）。本次将两个宽基完全拆分为板块 ETF 组合，宽基仅保留择时指导职责。

## Goals / Non-Goals

**Goals:**
- 588000/159915 改为 `guide_only`：入坑检测、拐点确认、退出信号、ETA 预测照常，但不再生成宽基本身订单。
- 新增板块 ETF 池（中信二级板块 → 代表 ETF 映射）与 combo 信号选筹，坑内资金按信号动态分配到板块 ETF。
- 复用现有 DCA 节奏（dca_strategy/trend_factor/resonance/macro）、退出机制（down_turn/fallback）与执行链路（legs 订单拆分）。
- 配置化：板块池、TOP N、单板块上限、combo 权重均可调，无需改代码。

**Non-Goals:**
- 不改动其他宽基（中证500/沪深300/中证1000/恒生/纳指等）的既有逻辑。
- 不做个股/行业指数级（非 ETF）落地；本变更只落到 ETF 可交易标的。
- 不重写回测引擎；`scripts/backtest_golden_pit_sector_*.py` 仅作为参数来源与后续验证工具。
- 不做前端 UI 重构；仅 `/golden-pit/status` 与报告展示兼容性扩展。

## Decisions

### 1. 新增 `SECTOR_ETF_POOL` 配置并保留向后兼容
在 `golden_pit_config.py` 新增 `SECTOR_ETF_POOL: Dict[str, Dict[str, Any]]`，键为中信二级板块名，值为 `{etf_code, name, exchange, data_source}`；初始含已回测 10 只（512480/588200/515880/512720/159852/159732/515030/159929/159886/512660）。`guide_only` 布尔字段加到 588000/159915 配置。`PIT_POSITION_SPLIT` 与 `SEMI_BOOST_INDICES` 保留定义但仅对非 guide_only 路径生效，避免破坏性删除。
- **备选**：硬编码映射 → 不灵活，无法快速加入新板块。**备选**：直接把板块并入 `SEMI_BOOST_INDICES` → 语义不同（后者是贪婪信号增强，前者是资金流选筹），合并会污染现有判定。

### 2. 独立 `golden_pit_sector_service.py` 承载选筹
新增服务模块负责：拉取中信二级板块资金流（`moneyflow_ind_dc`，复用现有市场服务）与板块行情（tushare `fund_daily`/指数行情）→ 计算 oversold120（板块指数 120 日百分位）与 mf5_norm（5 日资金流归一化）→ 输出 `combo 分数 = w_ovs × 超跌分 + w_mf × 资金流分`（权重可配置，默认 0.5/0.5）→ 按分数降序返回 TOP N。
- **备选**：逻辑内嵌 DCA 服务 → 职责耦合，回测脚本无法复用同一套打分；独立模块供生产与回测共用。

### 3. 复用 legs 订单拆分机制
`golden_pit_dca_service.py` 现有 legs 逻辑（index + 588200 + 512480）改为：对 guide_only 指数，index leg 金额置 0，板块 legs 由 `golden_pit_sector_service` 当日选筹结果生成（`金额 = daily_amount × sector_weight`，sector_weight 按 combo 分数归一化、单板块上限 50% 截断）。非 guide_only 指数路径不动。
- **备选**：为板块组合单独建 DCA 窗口 → 重复实现窗口进度/回退/重置逻辑，风险大；复用现窗口最省且一致。

### 4. 退出采用"宽基指导 + 板块独立"双层
宽基 `full_exit`/`stop_profit` 映射为组合级清仓（清空该宽基对应板块持仓）；板块 ETF 自身沿用 `exit_mode=down_turn`（连续 3 天回落清仓）+ `exit_fallback_days` 兜底。`/golden-pit/status` 对 588000/159915 增加 `guide_only=true` 与板块组合摘要字段，前端无破坏。

### 5. 灰度开关
新增配置 `GOLDEN_PIT_SECTOR_SPLIT_ENABLED`（默认 false）。开启前系统只计算并展示选筹结果（dry-run），不产生订单；人工确认回测/实盘信号稳定后置 true 切换执行。

## Risks / Trade-offs

- 板块资金流数据 T+1/缺失 → Mitigation：选筹使用最近可用交易日数据（与回测口径一致），数据不足 120 日的板块直接排除。
- 板块 ETF 流动性/折溢价导致执行损耗 → Mitigation：池内仅收录高流动性代表 ETF；沿用报告中的"执行损耗"统计持续监控，损耗过大的映射可配置替换。
- 全部板块信号不满足 → 空仓等待（不强制买入），报告提示"等待板块信号"，避免追入弱势板块。
- 迁移期存量宽基持仓 → Mitigation：切换后不再新增宽基买入，存量持仓按现有退出信号自然清仓，不强制市价砸盘。
- guide_only 宽基仍计入共振/评分统计的口径变化 → Mitigation：`get_score` 可交易指数统计先保持现状，观察期后再决定是否排除 guide_only。

## Migration Plan

1. 合并配置与信号模块：新增 `SECTOR_ETF_POOL`、`guide_only` 标记、`golden_pit_sector_service.py`（只读计算）。
2. 状态与报告扩展：`/golden-pit/status` 增加 guide_only 与组合摘要；晨报/定投报告展示当日选筹（dry-run）。
3. 执行切换：feature flag `GOLDEN_PIT_SECTOR_SPLIT_ENABLED=false` 部署，观察 dry-run 信号与回测一致性。
4. 灰度开启：588000/159915 改为板块 legs 下单，其余宽基不变。
5. 回滚：flag 置回 false 即恢复宽基直接买入路径（PIT_POSITION_SPLIT 仍保留）。

## Open Questions

- TOP N（默认 2）与单板块上限（默认 50%）是否需要按科创50/创业板分别回测调参（当前建议共享参数）。
- 科创50 与创业板是否各自维护板块池子集（如科创聚焦半导体/芯片/消费电子），还是共享池按 combo 排序（回测口径为共享池，先沿用）。
- combo 分数中超跌与资金流的权重（0.5/0.5）是否需按窗口阶段动态调整。
