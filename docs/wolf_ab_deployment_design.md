# 狼大 A+B 题材外溢系统 · 上线设计方案

> 版本：2026-11-05  
> 定位：题材 sleeve 的“源判定 → 外溢候选 → B 选目标/龙头 → 统一监控器 → 交易模块”生产设计。  
> 原则：A 判要不要切；B 判切到谁/买谁；仓位与卖出不由 LLM 决定。

---

## 1. 目标与边界

### 1.1 目标

```text
源题材高位/高潮/反抽
→ A 判 need_switch
→ Candidate Builder 给候选
→ B 在候选里选 target/leader
→ 统一监控器确认持仓/上下位
→ 交易模块执行
```

### 1.2 边界

| 模块 | 负责 | 不负责 |
|---|---|---|
| A agent | 源状态、need_switch、候选方向 | 选股、下单 |
| Candidate Builder | 候选池、overlap/首日/过滤 | 最终 target |
| B agent | target、leader、wind、mid | 源是否切换 |
| 统一监控器 | 持仓、上下位、卖出触发 | 选股 |
| 交易模块 | 买入、仓位、窗口、风控 | 改选股 |
| 趋势 sleeve | 主线/浪型/趋势仓 | 题材外溢 |

---

## 2. 生产拓扑

```text
09:45 / 10:30 / 11:00 / 13:30
        │
        ▼
theme_intraday_snapshot
        │  (breadth/limit/leader/amount)
        ▼
theme_source_agent (A)
        │  need_switch=false → 结束
        │  need_switch=true
        ▼
theme_target_agent (B)
        │  target / leader / wind / mid
        ▼
theme_ab_gate
        │  校验 schema / 股票代码 / 核心段 / 仓位
        ▼
theme_sleeve_selected (PG)
        │
        ├──► theme_sleeve_trade (买入)
        └──► theme_sleeve_monitor (持仓/上下位/卖出)
```

A/B 不直接下单，只产出结构化信号。

---

## 3. 数据与 PG 表

### 3.1 现有表

```text
theme_definition
track_definition
theme_membership
theme_snapshot
theme_sleeve_daily
theme_candidate_pipeline
paper_positions
paper_orders
position_levels
```

### 3.2 新增表

#### theme_ab_state

```sql
create table if not exists theme_ab_state (
  trade_date date not null,
  slot text not null,              -- 0945/1030/1100/1330
  source text,
  source_state text,
  need_switch boolean,
  confidence numeric,
  a_raw jsonb,
  created_at timestamptz default now(),
  primary key (trade_date, slot)
);
```

#### theme_ab_candidates

```sql
create table if not exists theme_ab_candidates (
  trade_date date not null,
  concept text not null,
  priority int,
  overlap_n int,
  target_purity numeric,
  first_day_absolute boolean,
  prior_launch_days jsonb,
  breadth numeric,
  limit_up int,
  meta jsonb,
  primary key (trade_date, concept)
);
```

#### theme_ab_signals

```sql
create table if not exists theme_ab_signals (
  trade_date date not null,
  slot text not null,
  source text,
  target text,
  leader text,
  wind jsonb,
  mid jsonb,
  mode text,                       -- spillover/independent
  status text,                     -- proposed/approved/rejected/executed
  reason text,
  b_raw jsonb,
  created_at timestamptz default now(),
  primary key (trade_date, slot)
);
```

### 3.3 source of truth

```text
PG 为唯一 source of truth
JSON 只做导出/回放/缓存
```

---

## 4. 提示词存放（PG）

提示词不进代码、不进镜像，统一存 PG。

### 4.1 表

```sql
create table if not exists agent_prompts (
  id bigserial primary key,
  name text not null,              -- a_source / b_target / shared_rules
  version text not null,           -- 2026-11-05.1
  content text not null,
  meta jsonb default '{}'::jsonb,
  active boolean default false,
  created_at timestamptz default now(),
  unique(name, version)
);
create unique index if not exists ux_agent_prompts_active
  on agent_prompts(name) where active;
```

### 4.2 读取

```python
from backend.app.services.agent_prompt_store import load_prompt
PROMPT = load_prompt("a_source")
```

`load_prompt`：

```text
按 name 取 active=true
找不到 → 抛错
禁止 fallback 到文件
```

### 4.3 发布

```text
新版本 insert active=false
校验通过后事务内：
  update old active=false
  update new active=true
```

### 4.4 回滚

```text
把上一版本 active=true
不需要重启服务
```

### 4.5 初始化

上线前执行一次：

```text
seed_agent_prompts()
```

从当前 A/B prompt 文本写入：

```text
a_source
b_target
shared_rules
```

之后只改 PG。

## 5. A agent 对接

### 5.1 输入

```json
{
  "date": "20261028",
  "slot": "1330",
  "source": "robot",
  "source_intraday": {},
  "candidate_meta": {},
  "holding": {
    "symbol": "600118.SH",
    "entry": 50.60,
    "pnl": 0.10
  },
  "wave_state": {},
  "prompt_version": "2026-11-05.1"
}
```

### 5.2 输出

```json
{
  "source": "robot",
  "source_state": "source_high",
  "need_switch": true,
  "confidence": 0.7,
  "step1_source_stalling": true,
  "step2_candidate_first_day": true,
  "first_day_candidates": [],
  "candidates": [
    {"theme": "商业航天", "priority": 1}
  ],
  "verdict": ""
}
```

### 5.3 规则来源

```text
config/prompts/themes/a_source.md
config/prompts/themes/shared_rules.md
```

必须包含狼大原话：

```text
高潮/炸板切外溢
大哥先涨，二哥不掉
细分题材还在 + 大哥/二哥梯队未断 → 不切
第一天总不至于怕
第一波不挣钱，第二波才是拿来挣钱
```

### 5.4 失败处理

```text
timeout / bad_json / schema fail
→ 不切换
→ 记录 theme_ab_state.status=error
→ 告警
```

禁止：

```text
LLM 失败 → 代码硬编码 target
```

---

## 6. B agent 对接

### 6.1 输入

只给 A 的候选：

```json
{
  "source": "robot",
  "candidates": [
    {"concept": "商业航天", "a_priority": 1, "first_day_absolute": true, "overlap": 54, "role_hint": {}},
    {"concept": "风能", "a_priority": 2, "first_day_absolute": true, "overlap": 37}
  ],
  "holding": {},
  "wave_state": {}
}
```

### 6.2 输出

```json
{
  "target": "商业航天",
  "mode": "spillover",
  "roles": {
    "leader": "600118.SH",
    "wind": ["688818.SH"],
    "mid": ["600879.SH", "601698.SH"]
  },
  "picks": []
}
```

### 6.3 硬校验

`theme_ab_gate` 校验：

```text
target 必须在 A candidates
leader 必须是 6 位股票代码
leader 必须是题材核心段
配套段只能 wind/mid
picks 不能为空才执行
```

不通过：

```text
status=rejected
不进入 trade
```

---

## 7. 统一监控器对接

现有：

```text
backend/app/services/theme_sleeve_monitor.py
```

### 7.1 职责

```text
读 PG paper_positions
读 position_levels 上下位
盘中触发 down_break / up_resistance
执行窗口 09:45–10:00 / 14:00–14:30
```

### 7.2 A/B 与监控器的关系

```text
A/B：选方向/选股/建仓
监控器：持仓后管理/卖出
```

A/B 不修改 `position_levels`；监控器不读 A/B 做卖出。

### 7.3 建仓后

`theme_sleeve_trade` 成交后：

```text
写 paper_positions
写 position_levels（agent/技术位）
通知 monitor 刷新持仓缓存
```

### 7.4 卖出

```text
down_break → 清仓
up_resistance → 减半
```

A/B 只提供：

```text
theme_id
role
entry
```

不作为卖出触发器。

---

## 8. 当前持仓对接

来源：

```text
paper_positions (account_id=stock)
```

传给 A/B 的字段：

```json
{
  "symbol": "600118.SH",
  "theme": "space",
  "role": "leader",
  "entry": 50.60,
  "current": 55.90,
  "pnl": 0.1047,
  "weight": 0.10,
  "hold_days": 1
}
```

用途：

```text
A：已有持仓时判断 hold/switch/trim
B：只在 A need_switch=true 时换仓
```

不允许：

```text
B 在没有 A 切换时改持仓
```

---

## 9. 浪型对接

现有：

```text
jobs/wave_advice_agent.py
data/mainline/<date>/track.json
theme_snapshot.mainline_status
```

A/B 输入增加：

```json
{
  "wave": {
    "mainline_status": "weak",
    "regime": "bear",
    "stage": "",
    "tradeable": false
  }
}
```

规则：

```text
wave 只作背景/仓位系数
不直接否决题材外溢
```

例外：

```text
mainline_status=panic / tradeable=false
→ A/B 可输出 probe，不满仓
```

---

## 10. 交易模块对接

现有：

```text
jobs/theme_sleeve_select.py
jobs/theme_sleeve_trade.py
```

### 10.1 仓位

不由 agent：

```text
first_day_probe  → 0.5
confirmation_add → 0.5
watch            → 0
```

### 10.2 买入

```text
theme_sleeve_trade
  → pre_trade_gate
  → calc-position
  → paper_orders
  → paper_positions
```

### 10.3 执行窗口

```text
09:45–10:00
14:00–14:30
```

### 10.4 买不了

```text
主板中军/ETF
否则不做
```

---

## 11. 调度

### 11.1 盘中

```text
09:45  A
10:30  A
11:00  A
13:30  A
13:45  B（仅 need_switch=true）
14:00  trade
```

### 11.2 收盘

```text
15:05  A 复核
15:10  B 复核
15:20  select
```

### 11.3 幂等

```text
(trade_date, slot)
唯一键
重复跑不重复下单
```

---

## 12. 失败与降级

| 失败 | 处理 |
|---|---|
| A timeout | 不切换，告警 |
| A bad_json | 不切换，告警 |
| B timeout | 不换仓，保留持仓 |
| B leader 非代码 | reject |
| B leader 配套段 | reject |
| PG 写失败 | 内存信号不 trade |
| 行情断 | 停 A/B，监控器独立跑 |

---

## 13. 上线方式

不做影子/paper 分阶段，直接正常上线。

上线前检查：

```text
1. agent_prompts 已 seed 且 active
2. 10/28 回归通过
3. PG theme_ab_* 表已建
4. theme_sleeve_monitor 持仓/上下位可读
5. theme_sleeve_trade 执行窗口/仓位正常
```

上线后：

```text
A/B 正常产出信号
theme_ab_gate 校验
select/trade 正常执行
monitor 正常管理持仓
```

首日观察：

```text
A timeout
B reject
target 为空
leader 非代码
成交回报
```

异常只告警，不自动改策略。

## 14. 监控指标

```text
A timeout 率
B reject 率
A need_switch 次数
B target 为空次数
leader 非代码次数
交易执行率
次日收益
最大回撤
```

---

## 15. 回归基线

```text
data/backtest/recovered_b_20261028.json
```

10/28 必须：

```text
target 商业航天
leader 600118
```

回归脚本：

```text
scripts/check_b_regression.py
```

失败不允许上线。

---

## 16. 文件索引

```text
docs/wolf_ab_deployment_design.md
docs/wolf_a_b_agent_design.md
PG agent_prompts: a_source / b_target / shared_rules
jobs/theme_source_agent.py
jobs/theme_target_agent.py
jobs/theme_ab_gate.py
jobs/theme_sleeve_select.py
jobs/theme_sleeve_trade.py
backend/app/services/theme_sleeve_monitor.py
scripts/backtest_ab_local.py
scripts/run_ab_portfolio.py
scripts/check_b_regression.py
```

---

## 17. 一句话

```text
A 判切不切
B 在 A 候选里选谁
监控器管持仓/卖出
交易模块管仓位/执行
prompt 文件化、版本化
PG 唯一 source of truth
```
