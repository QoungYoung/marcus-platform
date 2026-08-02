## Context

黄金坑回测 `simulate_dca()` 在 `backtest_golden_pit_ultimate.py` 中逐日模拟每笔交易的完整生命周期：DCA 建仓 → 持有 → 退出。当前它只记录最终退出时的 `return` 和 `max_drawdown`（最差单笔最终收益），不追踪持有期内的最大浮亏路径。实盘数据显示 intra-trade MAE 可达最终亏损的 1.5-2 倍。

DCA 执行服务 `golden_pit_dca_service.py` 已有三个安全制动（假信号、飞刀、累计截断），但 lump_entry 策略缺少专项的"拐点后反转"保护。历史数据表明 lump_entry 入场后 3 天内 greed 反转概率 12-38%，20 天内 69-100%。

深度入坑（≥30 天）在历史上出现过 5-9 次/指数，目前系统仅在报告中展示 `days_in_pit`，无告警机制。

## Goals / Non-Goals

**Goals:**
- 在回测中计算并报告每笔交易的 intra-trade MAE 及分布
- 为 lump_entry 策略添加"拐点后 3 天内反转"的安全制动
- 当 `days_in_pit >= 30` 时推送告警

**Non-Goals:**
- 不修改现有退出策略（exit strategy）逻辑
- 不修改 CHINA_INDICES 中的 per-index 参数阈值
- 不在前端 UI 中新增页面或组件
- 不新增数据库表或迁移

## Decisions

### 1. MAE 计算方式：在现有 simulate_dca 中内联追踪

**选择**：在 `simulate_dca()` 的 day-by-day loop 中，新增 `min_close` 变量追踪持有期内的最低收盘价，退出时计算 `max_adverse_excursion = (min_close / avg_entry - 1)`。

**替代方案**：事后对 trades 详情做独立分析脚本 → 拒绝了，因为回测时已有完整价格序列，内联追踪零额外成本。

**理由**：`simulate_dca()` 已经逐日遍历持有期（`for j in range(start_check, max_check)`），只需在遍历中更新 `min_close`、退出时写入 trade dict。报告层面在 `compute_metrics()` 中增加 MAE 分布桶（<-5%, <-10%, <-15%, <-20%, <-30%）的计数。

### 2. Lump entry 反转保护：监控窗口 3 天 + 连续下降 ≥2 天

**选择**：lump_entry 执行后（schedule_day=0），在后续 3 天内检查 greed 是否出现"连续 2 天下降"的反转模式。若触发，将剩余策略从 lump_entry 切换为 uniform_5。

**阈值来源**：历史数据分析：
- 拐点后 3 天内是否反转：这是"假拐点"的高发窗口
- 连续 2 天下降：平衡了敏感度（1 天可能是噪音）和及时性（3 天太慢）
- 切换为 uniform_5 而非 abort：因为虽然拐点可能不牢固，但贪婪仍在低位，完全放弃可能错过反弹

**替代方案**：
- 切换为 abort（放弃该窗口）→ 拒绝了，因为贪婪仍 < pit_greed，只是拐点不可靠
- 用 uniform_3 → 拒绝了，5 天更平滑，给拐点重新确认留出时间

**实现**：在 `golden_pit_dca_service.py` 的安全制动区域（falling_knife 检查之后）添加第四个制动。需要追踪 lump_entry 后每天 greed 的方向（上升/下降），用 2-bit 状态机（连续下降计数器）。

### 3. 深度入坑告警：复用现有报告推送通道

**选择**：在生成每日定投报告时（`golden_pit_dca_service.py`），检查每个指数的 `days_in_pit`。当 ≥30 天时，在报告的"操作建议"段中添加专项告警行。

**理由**：最简单、不增加新的推送通道。`_build_index_info()` 已经计算了 `days_in_pit`，直接复用。30 天阈值基于历史分布——中位数坑长度 14-18 天，30 天约为 P75-P90，属于"异常长"的范畴。

**替代方案**：通过 QQ Bot 单独推送 → 拒绝了，会增加通知噪音，放在日报中更合适。

## Risks / Trade-offs

- **[Risk] 反转保护误触发：lump_entry 后 greed 短暂波动 1-2 天又上升** → 系统切换到 uniform_5，失去了 l露mp_entry 的集中建仓优势。Mitigation：连续下降 ≥2 天而非 1 天，过滤单日噪音。
- **[Risk] MAE 指标给用户带来心理压力** → 看到历史上 27% 的交易浮亏超 -20% 可能导致过早手动干预。Mitigation：在报告中同时展示"恢复时间中位数 1-2 天"，强调 MAE 不等于最终亏损。
- **[Trade-off] 深度入坑 30 天阈值可能太宽松** → 中证1000 最长坑 50 天，30 天时可能只过了一半。但这正是告警的价值——提醒用户"这已经是不寻常的长时间"。

## Open Questions

无。三个改进均基于实盘数据验证，阈值选择有历史分布支撑。
