## Why

用户当前仓位低、想建一点仓位，但 wave=t_only 只允许做T、禁止"新建主升仓"。狼大在 t_only / 大4浪磨底期（当前 4-4，其自述"打底约一个月"）的实际做法不是干等空仓，而是：
1. **低位埋伏**：低位方向找辨识度最高的老龙头、缩量企稳不破前低，先小仓埋伏（"低位看逻辑"）；
2. **做T在场/低吸试仓**：在磨人标的里低吸高抛降成本、分批试仓（"强的留 弱的丢"）；
3. **等右侧**：突破主升确认（他描述"先破 4000-4020 吸增量、出货 4020-4070"，但这是**当时行情点位**，不是规则）才真正加仓。

现有系统在 t_only 下把这些"低吸建底仓/埋伏/切换建新仓"全部挡成不新建，与狼大实际打法不符；而把升级条件写成固定点位（4000-4020）又会随时间失效——**必须做成动态结构信号，杜绝硬编码**。

## What Changes

新增狼大式 t_only 期**建仓执行链**（三档状态机 + 动态升级 + 卖旧切换）：

- **三档仓位状态机**（同一标的/方向）：
  1. ambush（低位埋伏）：position_class LOW + confirm stage∈{缩量止跌,结构到位} + 缩量&不破前低 + 属主线候选 → 极小先手仓（≤净值1%，分批，env 可配）；
  2. trial（试仓）：stage∈{突破候选,确认} 或 ambush 升级 → 累计≤计划仓30%（分批，env 可配）；
  3. normal（主升建仓）：wave=build 或结构主升确认 → 放开正常建仓额度。
- **触发只经低吸信号**（254 触前低+缩量 / 253 大盘5min急杀），**禁追高买入**；defense/exit 不介入。
- **升级判定 = 动态结构信号（无点位硬编码）**：① wave_state.operation 转 build；或 ② 上证 close>MA200 且 MA20>MA60 且 vol≥1.2×20日均量 且 close≥近60日箱体上沿×0.995（箱体上沿=max(high[-60:]) 随行情滚动）。
- **切换建新仓（switch）**：持仓属"掉出 fusion TOP3 的旧方向 且 破位/资金流出" → 标卖腿（黄线/破位确认）；新上榜主线候选活跃股（stock_confirm 突破候选）走 254/253 低吸建新仓（无底仓→wolf_253_build），卖旧释放资金供新仓，强的留弱的丢；先 DRY 清单对照狼大再接通执行。
- **杜绝硬编码**：方向集=fusion TOP1∪TOP2（运行时）；阈值 env（TRIAL_SINGLE_PCT/TRIAL_CAP_PCT/AMBUSH_SINGLE_PCT/AMBUSH_CAP_PCT/结构窗口与倍数）；升级点位用滚动结构，禁止 4000/4020 字面量；标的→板块映射用概念表+名称关键词（不维护持仓→ETF 硬表）。

## Capabilities

### New Capabilities
- wolf-trial-ladder: 狼大 t_only 期建仓执行链——低位埋伏(ambush)→试仓(trial)→主升建仓(normal) 三档状态机、低吸触发限定、动态结构升级（无硬编码点位）、切换卖旧建新。

### Modified Capabilities
（无——本 change 不改既有 spec 需求，只新增能力与接入点。）

## Impact

- **新模块**：apps/main_line/tranche_ladder.py（三档状态机+额度记账+动态升级信号）、apps/main_line/switch_builder.py（切换卖旧建新评估，先 DRY）。
- **接入点**：TMonitor 253/254 低吸买腿（放行 ambush/trial）、wave/仓位 gate 交互、盘前/复盘报告（埋伏/试仓/升级状态）、rotation_switch_arm（卖腿+新方向布腿消费 switch 清单）。
- **配置**：env 化阈值；不新增点位硬编码。
- **运行位置**：worker（TMonitor/rotation/调度）+ 报告。
