# 主线内切换·个股级 — 生产接线建议（草案，待用户确认）

> 基于 backtest_rotation_switch_stock.py（E09-E12 全部 executed）+ v2 评测（±5 日 35/39=89.7%，8/8 aligned）
> 落地状态：**自动执行已启用 + rotation_switch_context 已注入 trade_graph/Pi 提示词**（2026-09-03，模拟盘）——tasks=29 rotation_switch_dryrun 工作日15:25；SWITCH_EXEC_ENABLED=1 时 plan 后自动：sell_plan clear/halve 先卖（保留做T 100股铁律由后端拦），主线内 room 链经 LOW/MID+非拥挤扫描后小仓买入（≤3只/100股起点）。参数默认见 config/switch_wolf_defaults.json（系统假设标注）。
> 原则：**链级决策 ≠ 个股资金流**；个股级只做新链内的选股排序。

## 1. 生产语义拆成两层
1. **链级决策（什么时候切）**
   - 输入：目标子方向（SUB_UNIVERSE）的 PIT 象限（拥挤/出货周期）、语料/日历（H1设备H2材料、业绩月后调仓等）、wave op；
   - 卖旧链触发：旧链 PIT 象限 ∈ {拥挤无空间, 拥挤有空间且空间恶化} 或 语料明示出货周期；
   - 买新链触发：日历/逻辑目标链（E10 国算、E11 材料、E12 半导体低吸）；
   - 数据源：data/rotation_quadrant_history_pit.json 生产版（每日按最新 PIT 快照刷新）。
2. **个股级选股（切到哪几只）**
   - 新链内候选：position LOW/MID 或 相对新链/卖出链的 20 日收益 ≤ 中位（rel-low=短期没涨）；
   - 过滤：PIT 公募核心拥挤(n≥4 且 float≥1%)剔除；
   - 输出：买入短名单（按 rel-low + 资金流 + 拥挤打分），每次 ≤ N 只。
- 卖出侧执行：旧链持仓按“链级决定”整链/部分减，不受个股短期资金流入否决（E10 实证：卖出时主力仍净流入）。

## 2. 生产接入点（均建议先 dry-run）
| 接入点 | 改动 | 灰度 |
|---|---|---|
| rotation_gate.py | verdict 增加 switch_stock 分支，输出链级决策+候选短名单 | ROT_SWITCH_DRY=1 先记不改 |
| trade_graph prompt | 注入“切换链级决策+个股短名单”上下文 | 同 rotation_gate 上下文 |
| 候选/长期池监控器 | intent=add_base/refill 且换仓路径=卖A买B（卖出先、买入后），经 check_entry_filters | P3_TIER_MODE 已有，新增 SWITCH_EXEC_ENABLED=0 |
| P3 intent | 新链买入走 refill_base/probe 小仓，不自动 full base | dry-run |
- 卖出联动：用 T+1 资金与 rotation_gate sell_guard 做二次确认（避免把“龙头死”误判为链级切出）。

## 3. 待确认清单
1. 链级卖旧是否允许“整链清”还是“只减到 50%”？（E10 Wolf 清麦米/CPO，E12 光 45→18 是减仓不是清仓）
2. 换仓执行顺序/资金复用：先卖后买、同日完成 vs 分 2 日？
3. 个股短名单数量上限（≤3-5？）与单票档位（probe≤3%？）
4. 是否需要一个“切换日历”（业绩月后调仓、H1/H2）人工维护表做链级触发源。
