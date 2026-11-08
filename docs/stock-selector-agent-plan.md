# 选股子 agent 方案（wolf_stock_selector）

> 目标：把“给定板块 → 候选池 → 谁可试探/谁等待/谁排除”这套人工选股流程，做成一个可复现的子 agent。
> 参考：`docs/ai-agent-lessons-from-b-rebound.md`（下称“账本”）、`docs/agent-time-windows.md`、`docs/ai-asof-trial.md`。
> 定位：**shadow / advisory 选股裁判**，不是自动交易，不接下单，不接 gate 的最终放行。

---

## 0. 结论

可以做，但要按账本的骨架做，而不是“让 agent 看一堆数据自由发挥”：

```
方向/板块（L0 主线 + L0.5 方向，自动）
   ↓
事实底稿（程序算，只给事实，不给结论）
   ↓
判据路由（程序先定段位/位置，再选规则分支）
   ↓
选股子 agent（只做个股层判断，允许“看不清”）
   ↓
程序校验（schema / 数字 / as-of / 规则 / 系统闸门）
   ↓
变更检测 → 推送（先 shadow，后 advisory）
   ↓
T+1/3/5 结果跟踪 → 每周失败复盘
```

**与 B反 agent 的区别**：B反 agent 判“段位/起点”，选股 agent 判“候选池里谁可试探、谁等待、谁排除”。两者共用同一套事实底稿、as-of、窗口、输出契约和验证方法。

---

## 1. 职责边界

### 做

- 输入一个**已给定的板块**（存储芯片 / 人形机器人 / …）和候选池（10-30 只）。
- 输出 **Top 3 + 观察池 + 排除池**，每只给：
  - `verdict`：`probe`（可试探）/ `wait`（等触发）/ `watch`（观察）/ `reject`（排除）。
  - 入场、止损、目标、仓位上限、时间止损。
  - **反证条件**（可观测）。
  - 规则命中/未命中 + 数据冲突。
- 只做**个股层**判断；板块层由人/主 line agent 给定。
- 只读；不写锚点、不改规则、不下单。

### 不做

- 不选主线、不选板块（不做“今天做存储还是机器人”）。
- 不预测收益、不给胜率、不打分、不用“量比≥1.3 就买”这类阈值触发式结论。
- 不自己取数（避免账本 §2.4/§2.5/§2.6 的空转判据、自我强化、未来数据）。
- 不覆盖 P2/P3 系统闸门；闸门 `allow_new=false` 时只能给 `watch`，不能给 `probe`。
- 不自动下单。

---

## 2. 架构：五层，各层独立失败模式

### L0 方向/板块（人 / 主 line agent）

- 输入：`sector`、`as_of`、`mode`、候选池来源（THS 概念 / 股票池 / 人工清单）。
- 输出：一个 `sector_facts.json`（板块宽度、benchmark、大哥/梯队、阶段）。
- 失败模式：板块选错。选股 agent 不负责纠错，只会在 `data_conflicts` 里报“板块与个股不匹配”。

> **可自动化版本**：L0 可以拆成 `wolf_mainline_select`（主线 top3）+ `docs/direction-selector-agent-plan.md`（L0.5 子方向选择）。L0.5 在固定科技子方向宇宙里给 `focus/watch/avoid`，选股 agent 只跑 `focus/watch` 的方向；L0/L0.5 用**自动一致性闸 + 系统闸门**替代人工确认，人只做审计、异常否决和规则变更。

### L1 事实底稿 builder（程序，确定性）

- 文件：`jobs/wolf_stock_select_facts.py`。
- 复用：`core/tushare_relay.py`（datahubco 优先、promax 兜底）、`data/fund_holdings/aggregate.json`、`data/crowding_pit/stock_crowd_*.json`、`indicator` API、腾讯/东财实时。
- 输出：`data/stock_selector/{as_of}/{sector}_facts.json`。
- 原则（账本 §3）：
  1. **同构**：所有候选一行、字段一致；不点名、不加权重、不标“重点关注”。
  2. **给演变，不给切片**：20/60 日位置、回撤、量能变化，不只给当天。
  3. **区分“已出现/目标/待确认”**：`pos60`、`dd60`、`ytd`、`stage` 都要带语义。
  4. **能算的都给，但别给结论**：MA、ATR、RS、拥挤、业绩、解禁、回购都算好；不下“可买”的结论。
  5. **口径守卫**：`mode` / `phase` / `same_day_executable` / `data_warning` / 单位。

### L2 判据路由（程序）

- 文件：`config/stock_selector/rules_*.yaml` + `jobs/wolf_stock_select_route.py`（可并入 facts）。
- 先定**段位/位置**，再选规则分支（账本 §2.1）：
  - `stage`: `washout`（洗盘末段）/ `rebound`（反弹初段）/ `breakout`（突破）/ `downtrend`（下跌浪）/ `high`（高位）。
  - `position`: `low` / `mid` / `high`（用 `pos60`、`dd60`、`ytd`、`stage`）。
- 再按 `sector` 路由：
  - 存储：五条逻辑腿 + 四层（原厂/设计/模组/设备材料）。
  - 机器人：筹码干净 + 大哥/梯队 + 宇树链 + Q3。
- 规则必须**可执行**（账本 §2.4）：每条规则绑定一个 `facts` 字段和算子；没有数据的规则不进入 prompt，标 `unavailable`。

### L3 选股子 agent（DSH subagent 或 `/chat`）

- 交互式：DSH `subagent`，无对话上下文，适合盲测。
- 定时：`PI_SERVER_URL/chat`，`session_id = selector_{as_of}_{sector}_{run_id}`，**每天新建，不复用**（避免账本 §2.5 自我强化）。
- **不给工具**（facts 已算好）；如必须给，只给只读、同 as-of 的 `indicator` 查询。
- `temperature=0`，`max_tokens=2000`，只输出 JSON；解析失败重试 1 次，仍失败则 `data_warnings` + 不推送。

### L4 程序校验（`jobs/wolf_stock_select_validate.py`）

- schema 校验：字段、类型、枚举。
- 数字校验：entry/stop/target 必须来自 facts 的价位或可计算比例；不能凭空造。
- as-of 校验：所有引用日期 ≤ `as_of`；`mode` 与 `phase` 一致；`window_assumption` 必须回填。
- 规则校验：`reject` 必须给出命中的硬规则；`probe` 不得违反 P2/P3。
- 冲突检测：同一票“低位 + 核心弱于板块”“主力流入 + Q3 未预告”等，必须出现在 `data_conflicts`。
- 闸门校验：`system_gate.allow_new=false` → `probe/confirm` 全部降级为 `watch`。

### L5 变更检测 + 推送 + 结果跟踪

- 变更键：`as_of + sector + top_pick.code + top_pick.verdict`（稳定短标签，账本 §2.11）。
- 无变化 → 静默；变化 → QQ 推短版（Top3 + verdict + 触发/反证）。
- 结果跟踪：`jobs/wolf_stock_select_outcome.py`，记录 T+1/T+3/T+5 相对板块 benchmark、最大回撤；只用于失败复盘，不作为胜率 KPI。

---

## 3. 事实底稿 schema（示例）

```json
{
  "as_of": "20261023",
  "mode": "post_close",
  "phase": "close",
  "same_day_executable": false,
  "sector": "人形机器人",
  "sector_benchmark": {"code": "562500.SH", "ret20": -0.034, "ytd": -0.079},
  "breadth": {"up_ratio": 0.875, "median_pct": 2.63, "vol_ratio_vs_prev": 1.46},
  "leader": {"code": "688836.SH", "name": "宇树科技", "role": "大哥", "pct": 20.0, "top_list_net_yi": 2.11},
  "ladder": ["003021.SZ", "688017.SH", "002050.SZ", "601689.SH"],
  "system_gate": {
    "p2_level": 2,
    "p3_tier": "G3",
    "allow_new": false,
    "reasons": ["大光破位大黑K", "margin_burst", "下跌1浪 defense"]
  },
  "candidates": [
    {
      "code": "300953.SZ",
      "name": "震裕科技",
      "layer": "丝杠/电机铁芯",
      "leader_chain": true,
      "price": 89.20,
      "pct": 11.92,
      "ytd": -0.266,
      "ret5": 0.144,
      "ret20": -0.074,
      "rs20_vs_benchmark": -0.041,
      "ma5": 82.96,
      "ma10": 81.26,
      "ma20": 87.90,
      "atr_pct": 0.067,
      "pos60": 0.423,
      "dd60": -0.189,
      "vol_ratio": 1.54,
      "amount5_yi": 0.70,
      "fund": {"n_funds": 7, "float_pct": 0.97, "max_pct": 0.48, "period": "20260630", "stale_days": 124},
      "earnings": {"q3_forecast": "预增 57.79-69.93%", "forecast_date": "20261013", "report_date": "20261029", "actual": null},
      "moneyflow_5d_yi": 0.70,
      "top_list": null,
      "unlock": null,
      "holder_trade": null,
      "buyback": null,
      "risk_flags": ["debt_to_assets=68.2%", "ATR=6.7%"]
    }
  ],
  "data_warnings": [
    "公募持仓截至 2026-06-30，非实时",
    "Q3 预告仅部分公司有；无预告≠没有风险"
  ],
  "note_unit": "金额 _yi=亿元；比例 _pct=百分数；ratio=倍数；价格=元"
}
```

**硬性要求**：

- 每个字段带单位/口径；`as_of` 与 `period` 分开。
- 没有的字段写 `null`，不写 0；agent 看到 `null` 必须说“缺”，不能用次优数据填（账本 §3.4）。
- 底稿不含“可买/不可买”“重点”“评分”。

---

## 4. 判据包（rules.yaml）

### 4.1 规则 schema

```yaml
- id: R6
  name: 核心必须强于板块
  source: "2025-12-06"
  quote: "这个票的走势一定要强于板块走势，板块同期要优于大盘走势"
  applies_when: "stage in [rebound, breakout, uptrend]"
  check: "candidate.rs20_vs_benchmark >= 0"
  severity: hard          # hard=reject / soft=扣分
  on_fail: reject         # reject | wait | watch
  known_cases:
    - "2026-10-23 兆易: -4.6pp -> reject"
    - "2026-10-23 澜起: +9.2pp -> pass"
```

### 4.2 规则分层

| 层 | 规则 | 例子 |
|---|---|---|
| 段位/位置 | 先定 `stage`，再选分支 | 下跌末段 vs 高位，同一形态含义相反（账本 §2.1） |
| 主线/方向 | 只做主线、主线内分层 | R4、存储四层、机器人大哥/梯队 |
| 核心/龙头 | 市值前 5、营收拐点、绑定板块、辨识度 | R5、R10、R11 |
| 筹码 | 公募拥挤、年内涨幅、套牢盘、有无大哥 | R9、机器人“筹码干净” |
| 业绩 | 预告/快报/财报、业绩雷、Q3 窗口 | R14、存储“财报底/映射顶” |
| 量价 | 缩量止跌→缩转放→过前高→站稳 3 天 | R16、R18、R19 |
| 仓位/闸门 | P2/P3、三方向、≤6 票、先控回撤 | R23、系统闸门 |
| 存储专属 | 炒存货 vs 设计 vs 设备材料、两长扩产、外资映射 | R24、R25、R26 |
| 机器人专属 | 筹码干净、大哥宇树、梯队、Q3 密集 | 10/23 楼段 |

### 4.3 规则变更流程

1. `jobs/wolf_thread_sync.py` 同步新发言。
2. 人工从语料里摘“可执行判据”，写进 `rules_*.yaml`（带日期/原话）。
3. **误杀测试**：拿已知判例逐条跑，看有没有把专家认可的样本排掉（账本 §2.3）。
4. 版本号 +1；旧版本保留；规则变更记录进 `docs/stock-selector-rules-changelog.md`。

---

## 5. 子 agent 的 prompt 契约

### 5.1 system prompt（固定）

```text
你是狼大交易体系的“选股子 agent”。你只做个股层判断，不做主线选择，不下单。

硬规则：
1. 先定段位/位置，再选判据分支；段位未定，不许下结论。
2. 只用 facts.json 中存在的字段；缺数据就说“缺”，不要用次优数据填。
3. 每条判断带日期 + 数字（价位/比例）；允许“看不清/等待”，这常常是正确产出。
4. 不得打分、不得给胜率、不得用“量比≥1.3就买”这类阈值触发式结论。
5. 必须给反证条件，且可观测（“收盘跌破 X”，不是“感觉不对”）。
6. system_gate.allow_new=false 时，只能给 watch，不能给 probe/confirm。
7. 输出严格 JSON，不要解释文字；数据冲突写进 data_conflicts。
```

### 5.2 user prompt（模板）

```text
板块：{sector}
as_of：{as_of}
mode/phase：{mode}/{phase}（same_day_executable={same_day_executable}）

事实底稿：
{facts_json}

判据（只列 applies_when 命中的规则）：
{rules_json}

问题：在这个板块的候选池里，哪些可以 probe，哪些 wait，哪些 watch，哪些 reject？
输出 JSON schema：
{output_schema}
```

### 5.3 输出 schema

```json
{
  "as_of": "20261023",
  "sector": "人形机器人",
  "window_assumption": {"phase": "close", "same_day_executable": false, "ok": true},
  "segment": {"stage": "rebound", "position": "mid", "evidence": "板块20日-3.4%，10/23放量普涨"},
  "top_pick": {
    "code": "300953.SZ",
    "name": "震裕科技",
    "verdict": "probe",
    "why": [
      "Q3预增57.8-69.9%（20261013）",
      "PE_TTM 30.2，机器人链最低一档",
      "公募7只/0.97%，年内-26.6%，60日位置0.42",
      "10/23 +11.92%，量1.54x，站上MA20 87.90"
    ],
    "entry": "86-88（回踩MA20）或突破89.96回踩不破89",
    "stop": "收盘跌破79.6；跌破83减半",
    "target1": "95-96",
    "target2": "103.3",
    "size_pct": 10,
    "time_stop": "3-5个交易日；10/29报告前减",
    "counter_evidence": [
      "若跌破83，说明10/23放量反包失败",
      "若Q3实际低于预告下限，逻辑失效",
      "若机器人板块上涨家数<50%，不成立"
    ]
  },
  "alternatives": [
    {"code": "002050.SZ", "name": "三花智控", "verdict": "wait",
     "why": ["5日主力+4.5亿", "Q3 10/29未预告", "公募11只/1.52%", "10/23收盘91%分位"],
     "trigger": "回踩33.8-34.3不破33.3，或Q3后放量站上37.1"}
  ],
  "watchlist": [
    {"code": "603667.SH", "name": "五洲新春", "verdict": "watch",
     "why": ["公募1只/0.08%，筹码最干净", "机器人收入仅1.3%，PE241", "Q3 10/28未预告"]},
    {"code": "688836.SH", "name": "宇树科技", "verdict": "watch",
     "why": ["大哥，5日主力+12.2亿", "次新/PE432/Q3未预告，只做风向标"]}
  ],
  "reject": [
    {"code": "300718.SZ", "name": "长盛轴承", "reason": "Q3 10/27未预告，事件风险；流动性0.48亿"}
  ],
  "rule_hits": [
    {"id": "R5", "result": "pass", "evidence": "市值218.7亿，H1净利+100.3%"},
    {"id": "R6", "result": "fail", "evidence": "rs20=-4.1pp，弱于机器人ETF"}
  ],
  "data_conflicts": [
    "震裕 rs20 弱于板块，但 10/23 单日强于板块；按 R6 仍判 fail，需等相对强度转正"
  ],
  "system_gate": {"allow_new": false, "effect": "probe 降级为 watch"}
}
```

---

## 6. 验证方案（别问胜率，账本 §4）

### 6.1 已知判例集（先 10-15 个）

| 日期 | 板块 | 标的 | 人工/狼大结论 | 期望 agent |
|---|---|---|---|---|
| 2026-10-23 | 机器人 | 震裕科技 | 可试探（业绩+估值+筹码） | probe（闸门允许时）/ watch（闸门禁止时） |
| 2026-10-23 | 机器人 | 三花智控 | 等 Q3/回踩，不追 | wait |
| 2026-10-23 | 机器人 | 五洲新春 | 筹码干净但基本面弱，只做短差 | watch |
| 2026-10-23 | 机器人 | 中大力德/长盛 | Q3 10/27 未预告，等 | wait/reject |
| 2026-10-23 | 机器人 | 宇树科技 | 大哥，只看不做 | watch |
| 2026-10-23 | 存储 | 兆易创新 | 核心候选，但 R6/R7 不过 | watch/reject |
| 2026-10-23 | 存储 | 澜起科技 | 相对强但公募最拥挤 | reject |
| 2026-10-23 | 存储 | 德明利 | 业绩落地但 5 日+22%，不追 | wait |
| 2026-05-09 | 存储 | 江波龙 | 外资映射，财报底/映射顶 | probe（映射期）/ watch（映射转弱） |
| 2025-11-21 | 存储 | 模组 vs 设备 | 模组炒存货，设备优先 | reject 模组 / watch 设备 |
| 2025-05-29 | 机器人 | 泛科技二买 | 筹码干净后二买 | wait→probe（二买确认） |

### 6.2 盲测

- 新建 subagent 会话，**不给日期身份、不给结论**，只给 facts + rules。
- 不给“这是不是 B反/能不能买”的提示。
- 记录：是否命中已知判例、是否给出可观测反证、是否报告数据冲突。

### 6.3 留出与换品种

- 规则是看着失败案例改的，必须留 3-5 个**设计时没看过**的日期/标的。
- 至少覆盖：存储上行、存储下行、机器人上行、机器人下行、次新/大哥、模组/设计。
- 换年份/换板块各 1 次（账本 §4.3）。

### 6.4 失败样例优先

- 每次修正必须来自一个具体失败：误杀、误放、数据冲突、窗口错、单位错。
- 记录到 `docs/stock-selector-failure-log.md`，每条形如：
  `日期 | 标的 | 期望 | 实际 | 根因（规则/数据/prompt） | 修正 | 回归 case`。

### 6.5 指标（不是胜率）

- **判例一致率**：已知判例中，agent 的 verdict 与人工一致的比例。
- **误杀数**：专家认可但 agent reject 的样本数（越少越好）。
- **误放数**：专家 reject 但 agent probe 的样本数。
- **反证质量**：反证是否可观测、是否带数字。
- **数据冲突发现数**：agent 主动报告矛盾的数量。
- **稳定性**：同 facts 两次运行 verdict 一致率（temperature=0）。

---

## 7. 工程落地

### 7.1 文件

```
config/stock_selector/
  rules_common.yaml
  rules_storage.yaml
  rules_robotics.yaml
  output_schema.json
jobs/
  wolf_stock_select_facts.py      # L1 事实底稿
  wolf_stock_select_route.py      # L2 判据路由（可并入 facts）
  wolf_stock_select_agent.py      # L3 调 /chat 或 DSH subagent
  wolf_stock_select_validate.py   # L4 校验
  wolf_stock_select_outcome.py    # L5 结果跟踪
data/stock_selector/{as_of}/
  {sector}_facts.json
  {sector}_decision.json
  {sector}_validation.json
docs/
  stock-selector-agent-spec.md    # 本方案
  stock-selector-rules-changelog.md
  stock-selector-failure-log.md
```

### 7.2 数据源映射

| 字段 | 来源 | 备注 |
|---|---|---|
| 日线/复权/MA/ATR | datahubco `daily` / `pro_bar` | 盘中用腾讯/东财，历史用日线 |
| 换手/市值/PE | datahubco `daily_basic` | 盘后 |
| 主力净流入 | datahubco `moneyflow` | lg+elg |
| 概念成分 | datahubco `ths_member` / `stock_pool.db` | 板块候选池 |
| 公募拥挤 | `data/fund_holdings/aggregate.json` + `data/crowding_pit/*` | 季度/PIT，标 `stale_days` |
| 业绩预告/快报 | datahubco `forecast` / `express` | `null` = 未预告，不是“没问题” |
| 披露日期 | datahubco `disclosure_date` | Q3 窗口 |
| 解禁/减持/回购 | datahubco `share_float` / `stk_holdertrade` / `repurchase` | 风险/加分 |
| 龙虎榜 | datahubco `top_list` | 只在异动日有 |
| 系统闸门 | 本地 `p2_entry_gate` / P3 / `check_entry_filters` | 只读，不覆盖 |
| 实时 | 腾讯 `qt.gtimg.cn` / 东财 `push2his` | 盘中 `tail_preview/tail_confirm` |

### 7.3 调度

| 任务 | 时间 | 说明 |
|---|---|---|
| facts | 15:10 | 盘后日线；缺失则非零退出，不推过期底稿 |
| route | 15:12 | 段位/规则路由 |
| agent | 15:15 | `/chat`，JSON 输出 |
| validate | 15:20 | schema/数字/as-of/闸门 |
| push | 15:25 | QQ 短版；无变化静默 |
| outcome | T+1/3/5 | 结果跟踪 |
| intraday 可选 | 14:40 | `tail_preview`，只预警不下结论 |

### 7.4 PG 表（append-only）

```sql
create table if not exists stock_selector_runs (
  id bigserial primary key,
  as_of date not null,
  sector text not null,
  mode text not null,              -- post_close / tail_preview / replay
  facts_hash text not null,
  decision jsonb not null,
  validation jsonb not null,
  system_gate jsonb not null,
  created_at timestamptz default now(),
  unique (as_of, sector, mode, facts_hash)
);

create table if not exists stock_selector_outcomes (
  id bigserial primary key,
  run_id bigint references stock_selector_runs(id),
  code text not null,
  verdict text not null,
  t1_ret numeric, t3_ret numeric, t5_ret numeric,
  t5_rel_benchmark numeric,
  max_dd numeric,
  recorded_at timestamptz default now()
);
```

### 7.5 幂等/失败/静默

- `on conflict do nothing`；唯一键用语义键（`as_of+sector+mode+facts_hash`）。
- 数据缺失 → 非零退出 + QQ ❌，不输出“看起来正常”的过期报告。
- 无变化 → 静默；变更键 `as_of+sector+top_pick.code+top_pick.verdict`。
- `--dry-run` 默认；`--apply` 才写库/推送。
- dry-run 也跑真实链路（账本 §7）。

---

## 8. 分期实施

| 阶段 | 工期 | 产出 | 验收 |
|---|---|---|---|
| **P0 规则/契约** | 0.5-1 天 | `rules_*.yaml` + `output_schema.json` + 本方案 | 26 条规则每条有 `check`；已知判例误杀测试表 |
| **P1 事实底稿** | 1-2 天 | `wolf_stock_select_facts.py` | 10/23 存储/机器人两份 facts 可复现；字段/单位/as-of 完整 |
| **P2 影子 agent** | 1-2 天 | `wolf_stock_select_agent.py` + 校验 | 10 个判例盲测；误杀/误放清单；不推送 |
| **P3 咨询推送** | 1 天 | QQ 短版 + `stock_selector_runs` | 每天 1 条；无变化静默；人工可复核 |
| **P4 结果跟踪** | 持续 | T+1/3/5 + 失败日志 | 20-30 个 case 后评估是否接 gate；**永不自动下单** |

---

## 9. 不做什么 / 已知失效条件

1. **不让 agent 选主线**：主线由人/主 line agent 给定；agent 只在候选池里选。
2. **不让 agent 自己取数**：避免空转判据、自我强化、未来数据（账本 §2.4/§2.5/§2.6）。
3. **不让 agent 写锚点/写库**：程序判“够不够格”，agent 判“是什么”，人保留否决权。
4. **不把描述当筛选器**：相位表/形态描述只用于“窗口内识别”，不做全历史扫描（账本 §2.2）。
5. **不为回测好看加规则**：每条新规则先跑已知判例，误杀就说明测的不是专家的东西（账本 §2.3）。
6. **不混窗口**：14:50 只 `tail_preview`，15:00 `close` 只能给次日动作（`docs/agent-time-windows.md`）。
7. **不混单位/键**：`_yi`/`_pct`/`ratio` 写进字段名；唯一键用语义键，不用浮点价格。
8. **不打分/不问胜率**：用判例一致率、误杀/误放、反证质量、数据冲突发现数。
9. **不自动交易**：P2/P3 是自动硬闸；agent 只给 `watch/probe`，执行层受仓位上限和 kill switch 约束；人只做审计。

**已知失效条件**（账本 §8）：

- 专家判例依赖盘口/资金身份（谁在买、GJD 托不托），我们拿不到 → 只能近似。
- 公募拥挤数据是季度/PIT，非实时；Q3 预告只覆盖部分公司。
- 宇树等次新股没有公募持仓数据；龙虎榜只在异动日有。
- 语料会过期（当前同步到 2026-10-16）；新结构/新说法需要人工入规则。
- agent 可能自证：因此必须 stateless、as-of、程序校验、人工否决。

---

## 10. 与现有系统的关系

- **复用**：`core/tushare_relay.py`、`data/fund_holdings/aggregate.json`、`data/crowding_pit/*`、`indicator` API、`jobs/wolf_brebound_1450.py` 的 facts 风格、`PI_SERVER_URL/chat`、`marcus-panel-tools` 只读工具。
- **不替代**：主 line 判定、P2/P3 闸门、下单链路。
- **后续**：如果 20-30 个 case 的判例一致率稳定、误杀可控，可把 `probe` 结果作为 `wolf_confirm_pick` 的候选输入；执行层受 P2/P3 与自动闸约束，人只做审计。

---

## 附：一句话

**把“选股”拆成“程序给事实、规则给分支、agent 给判断、程序给校验、自动闸给否决”，先影子跑 20-30 个判例，再谈推送和 gate——这就是这套流程能做成子 agent 的方式。**
