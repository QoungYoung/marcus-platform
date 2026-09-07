# Tasks

## 1. tranche_ladder 核心模块
- [x] 新建 apps/main_line/tranche_ladder.py：
  - [x] tier_for(symbol) -> 'ambush'|'trial'|'normal'|'none'：读取 position_class_result(stage/LOW)、stock_confirm_result(stage)、fusion TOP1∪TOP2、拥挤黑名单；defense/exit→none
  - [x] escalate_signal() -> (bool, triggers[])：wave=build 或 结构主升确认(close>MA200 ∧ MA20>MA60 ∧ vol≥1.2×20日均量 ∧ close≥近60日箱体上沿×0.995)——上证日线滚动计算，窗口/倍数 env 化，零点位字面量
  - [x] allowed_buy_volume(symbol, trigger_kind, quote, ledger) -> int：仅 254(custom_prevlow)/253(custom_m5dump) 且档位∈{ambush,trial} 时给档位上限内量；其余 0（追高不放行）
  - [x] 额度记账 read/write data/tranche_state.json（bought_amt/batches 按档位累计，升级累加不重复）
- [x] env 化：AMBUSH_SINGLE_PCT=1 / AMBUSH_CAP_PCT=10 / TRIAL_SINGLE_PCT=2 / TRIAL_CAP_PCT=30 / AMBUSH_SHRINK_MAX=0.7 / ESCALATE_VOL_RATIO=1.2 / ESCALATE_BOX_RATIO=0.995 / MA 与箱体窗口(20/60/200)
- [x] 单元/冒烟：tier_for/escalate_signal/allowed_buy_volume 三组用例

## 2. TMonitor 低吸买腿接入
- [x] TMonitor 253/254 低吸执行路径（含无底仓→wolf_253_build 分支）接入 tranche_ladder.allowed_buy_volume：
  - [x] wave∈{t_only,side} 且档位 ambush/trial → 以档位上限量放行（覆盖原 30% 做T量）
  - [x] defense/exit 或非低吸 trigger → 维持原 gate 拦截（不放行）
- [x] 升级联动：escalate_signal 成立后，normal 档允许正常建仓额度（不再受 ambush/trial 上限）；盘前重算写入 tranche_state

## 3. switch_builder 评估（DRY）
- [x] 新建 apps/main_line/switch_builder.py：
  - [x] 旧方向走弱：持仓方向掉出 fusion TOP3 且（close<MA20 或 5日资金负 或 confirm 证伪）→ sell_old；仍强→keep
  - [x] 新方向候选：TOP1∪TOP2 内 stock_confirm 突破候选/确认活跃股、非拥挤 → buy_new（建议 254/253 低吸建新仓）
  - [x] 输出 data/switch_builder_plan.json {sell_old, keep, buy_new, tiers, escalate}；默认 DRY（SWITCH_AUTO_EXEC=0 不自动执行）
- [x] 调度：盘前(08:1x)或复盘评估一次；报告输出清单

## 4. 报告接入
- [x] 盘前/复盘报告增加"建仓执行链"块：三档状态(标的/批次/额度)、升级信号(yes/no+触发项)、switch 清单(sell_old/keep/buy_new)
- [x] 与既有 wave 调档块/个股确认链上下文共存

## 5. 验证与上线
- [x] 用 09-04/09-07 数据回放：tier_for 对各持仓/候选标的分档正确；escalate_signal 今日是否成立；switch 清单对照狼大当日发言（科技主攻/消费滞后）
- [x] DRY 观察数个交易日：清单与狼大行为对照无大偏差
- [x] 生产上传 + worker 重启 + 报告可见后，由用户确认再开 SWITCH_AUTO_EXEC/完全接通
- [x] 全量 validate（openspec validate）