## Why

黄金坑回测用最终离场收益计算 max_drawdown，严重低估了入场后的真实浮亏。实盘数据显示：中证1000 27%的历史交易入场后最大浮亏超过-20%，创业板指40%的交易浮亏超过-10%，但回测只报告了-12%~-18%的"最差单笔最终亏损"。同时，一次性打入（lump_entry）策略在拐点确认后全仓进入，却没有任何针对"拐点后再次反转"的专项保护——历史上89%的 lump_entry 交易在20天内都经历了 greed 再度反转。此外，当前纳斯达克入坑14天、中证1000入坑8天的"深度入坑"场景缺乏系统级告警。

## What Changes

- **回测新增 intra-trade MAE 指标**：追踪每笔交易持有期内的最大浮亏（Maximum Adverse Excursion），与最终收益一起报告
- **一次性打入反转保护**：lump_entry 策略在拐点确认执行后，若3天内greed再次连续下降超过2天，自动将剩余未投仓位从 lump_entry 切换为 uniform_5
- **深度入坑告警**：当任一指数连续入坑超过30天时，推送告警通知并建议人工复核参数
- 回测报告新增 MAE 分布统计（MAE<-5%/-10%/-15%/-20%/-30%的比例）

## Capabilities

### New Capabilities
- `golden-pit-intra-trade-risk`: 回测追踪入场后持有期内的最大浮亏（MAE），替代仅看最终收益的单一视角

### Modified Capabilities
- `golden-pit-safety-brake`: 新增"一次性打入后反转保护"制动规则——lump_entry 执行后3天内若greed连续下降≥2天，切换为 uniform_5
- `golden-pit-dca-schedule`: 新增深度入坑（≥30天）的系统告警与人工复核建议

## Impact

- **回测脚本**: `scripts/backtest_golden_pit_ultimate.py` — `simulate_dca()` 和报告生成逻辑
- **DCA 服务**: `backend/app/services/golden_pit_dca_service.py` — 新增 lump_entry 反转保护检查
- **定时任务**: `config/tasks.yaml` — 可选的深度入坑告警推送
- **无 API 变更、无数据库迁移、无前端变更**
