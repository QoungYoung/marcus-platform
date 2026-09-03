# P2 完整接入当前交易 — 集成设计

> 生成：2026-09-03 · 目标：把主线/浪型/高低位/轮动拥挤/风控/宏观 六组 P2 模块变成"交易执行前的一道统一闸门"，而不是散在各处只做 prompt 提示。
> 现状审计：Pi/auto_trade 走 trade_graph（有 wave gate/rotation/risk/macro 上下文）；但 candidate_pool_monitor / long_term_pool_monitor 只调 check_entry_filters 就自动买，wave/rotation/macro 没有硬进入 → 这是当前最大接入缺口。

## 1. 总架构：所有"新开仓通道"只认一个 P2 闸门

候选/池刷新 → check_entry_filters（技术/资金/形态 + fail-closed） → P2 Gate v1（wave_level + rotation + risk + systemic + macro → block/multiplier/reasons） → calc_position → 下单

- 所有"新开仓"都过同一道 P2 Gate（Pi、auto_trade、候选池自动通道）；
- T 仓（t_monitor 249/250/252/253/254）单独走 T 通道，保留狼大"下跌中底仓做T"的资格；
- 硬拦在代码层（不是 prompt），命中即拒绝并记录原因。

## 2. 每个模块在闸门里的角色

| 模块 | 输入 | 输出 | 硬拦规则（v1 建议） | 软规则 |
|---|---|---|---|---|
| 浪型 wave | wave_state.json | op/gate | defense/exit → block 新开仓（现 trade_graph 已做，补到 check_entry_filters） | t_only/side → 降仓或标注 |
| 轮动 rotation | crowding_blacklist v2 + rotation_quadrant | 拥挤无空间/拥挤有空间 | 公募核心拥挤+高位无空间 → block（已上线） | 拥挤但有空间 → probe |
| 风控 risk | risk_flags | earnings_bad/ST/公告 | 命中 → block（已上线） | review 档等确认 |
| 系统性风险 | systemic_risk.json | level | level2（大光破位大黑K）→ block 新开仓 | level1 → 只 T/防御 |
| 宏观 macro | macro_state.json | margin_burst/lhb_foreign_sell/gjd_withdraw/yield_spike | ①margin_burst → block 新开仓（杀杠杆不接飞刀）②gjd_withdraw+无政策底 → block LOW 新埋伏 ③lhb_foreign_sell → 只拦海外链/红利核心类候选 | yield_spike → 降级 0.5 |

## 3. "方向感知"的 macro 拦（避免误伤）

lhb_foreign_sell（外资撤）不能拦所有票，只拦"外资主导方向"：
- 候选概念命中【海外链/海外映射】：CPO、光通信、英伟达链、PCB(海外客户)、存储映射 → block/review；
- 候选概念命中【红利/核心资产】：白酒、银行、保险、红利/低波、中字头 → block/review；
- 其它方向（国产半导体/国算/材料/自主）→ 不受 lhb_foreign_sell 影响。

概念→方向表放 data/p2_macro_direction_map.json（后续可由 SUB_UNIVERSE + 手维护合并）。

## 4. 执行触点改造清单

1. backend/app/api/indicator.py：在 risk_flags/crowding 之后插 P2 Gate 剩余项（wave/macro/systemic），失败记录 data_unavailable。
2. backend/app/services/p2_entry_gate.py（新）：单点函数 p2_gate_check(symbol, ts_code, context=None)，返回 block/multiplier/reasons；供 indicator 与 trade_graph 共用。
3. trade_graph.node_check_safety_gates：把重复的 wave 判断换成调 p2_entry_gate，保证与 check_entry_filters 同源。
4. macro 刷新：工作日 15:06 已自动；在盘前扫描里把最新 macro_state 快照一起展示（morning_diagnosis + market_scan）。
5. P2 Gate 日志：每次命中写 p2_gate_log（时间/标的/门类/原因/multiplier），用于观察校准（阈值 10/10 是语料小样本，仍需实盘样本）。

## 5. 灰度/回退

- P2_GATE_MODE=1 开启硬拦（默认）；=0 只记录不拦（dry-run）；
- 观察 2 周，主要看：
  - margin_burst 命中时是否真的避开了"接飞刀"；
  - lhb_foreign_sell 是否误伤国产替代/国算；
  - T 通道不受影响（有底仓继续可 T）。

## 6. 不建议做的事（避免过度工程）
- 不在 t_monitor 里加宏观硬拦——Wolf 杀杠杆期仍做 T/低吸，只有"新开仓"需要挡；
- 不把 wave defense 硬拦扩展到 T 腿（底仓做 T 是狼大核心）；
- 不把所有 macro 开关都设成 block（只 margin_burst/gjd_withdraw/方向性 lhb 拦）。

## 7. 实施顺序（建议 3 步）
1. Step 1：建 p2_entry_gate.py，把 wave/macro/systemic 判成统一结果，indicator.py 接入；auto 通道自动获得硬拦。
2. Step 2：方向感知（海外链/红利核心 map）+ p2_gate_log + morning/market_scan 展示。
3. Step 3：观察 2 周校准阈值后，把 P2_GATE_MODE 固定为 1，并写 tuning 报告。
