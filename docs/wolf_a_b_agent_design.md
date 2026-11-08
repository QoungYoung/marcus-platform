# 狼大题材外溢 A+B Agent 开发文档

> 版本：2026-11-04  
> 目标：把“源题材高潮/反抽 → 外溢候选 → B 选目标/龙头”做成可回测、可盘中滚动、可接生产的链路。  
> 原则：不编数据、不编狼大规则；A 判“要不要切”，B 判“切到谁/买谁”。

---

## 1. 总体架构

```text
源题材 facts
   │
   ▼
A agent（源状态 / need_switch / 候选方向池）
   │ need_switch=true
   ▼
Candidate Builder（重叠度 + same_chain/adjacent 过滤 + 首日/冷启动）
   │
   ▼
B agent（target / leader / wind / mid / picks）
   │
   ▼
theme_sleeve_select（确定性过滤，不再用 agent 仓位）
   │
   ▼
theme_sleeve_trade（仓位按题材策略，不按 agent）
```

关键分工：

| 角色 | 负责 | 不负责 |
|---|---|---|
| A | 源是否高潮/反抽/死亡；是否切换；候选方向池 | 选具体题材、选股 |
| Builder | 把相邻候选整理成 B 可用的数据包 | 最终选择 |
| B | 从候选里选 target、leader、wind、mid | 判断源是否切换 |
| select | 硬规则过滤、去重、输出 selected | 决定源状态 |
| trade | 执行、仓位、窗口、风控 | 选股 |

---

## 2. A agent

### 2.1 输入

- 源题材分支的 15 分钟序列：
  - breadth
  - 价格在涨停数
  - ret1
  - leader/wind 状态
- 历史源状态（至少前 5 个交易日）：
  - healthy / rebound / climax / wind_dead / leader_dead
- 当日盘中滚动快照（09:45 起每 15 分钟）
- 禁止使用未来数据。

### 2.2 输出

```json
{
  "source": "机器人链",
  "source_state": "rebound",
  "need_switch": true,
  "is_rebound": true,
  "prior_dead_date": "20261026",
  "confidence": 0.72,
  "candidates": [
    {"theme": "半导体", "why": "", "priority": 1},
    {"theme": "工业母机", "why": "", "priority": 2},
    {"theme": "商业航天", "why": "", "priority": 3}
  ]
}
```

### 2.3 状态定义

```text
healthy：
  源分支同步、breadth 高、涨停增加、龙头/风向标未死

climax：
  高位/广泛高潮：涨停家数多、炸板群、无晋级、整体亢奋
  不是“2-3 家涨停 + 龙头封死”

rebound（新增，必须单独判）：
  前 1–5 日出现过 wind_dead / leader_dead
  今天高开/冲高后走弱
  涨停数不增
  风向标未封或回落
  无晋级
  → 即使 breadth 高、龙头封死，也只能判 rebound
  → action=avoid/trim，new_buy=false，need_switch=true

wind_dead：
  风向标死

leader_dead：
  龙头死
```

### 2.4 滚动节奏

```text
09:45  第一根 15 分钟：只能判候选
10:30  若涨停数不增、ret1 回落、风向标未封：可判 rebound
11:00  更稳
13:30  确认
14:30  最后确认窗口
```

最早可判：

```text
10:30–11:00
```

不是 09:45 一根就下结论。

### 2.5 10/28 实测

- 10/26：robot_actuator = wind_dead
- 10/28 09:45：减速器 breadth 0.943、涨停 10、ret1 +4.22%
- 09:45–13:30：涨停数 0 增量（10→10 / 2→2）
- ret1：4.22→4.15→4.11
- 执行器风向标：+14.05%→+13.23%→+12.53%，全程未封
- A 判定：rebound / need_switch=true / confidence 0.72

没有 rebound 状态时：

```text
A 判 healthy / need_switch=false
→ B 不触发
→ 系统买源题材
```

---

## 3. Candidate Builder

### 3.1 候选来源

从全市场概念中计算：

```text
source_members
target_members
overlap = source ∩ target
target_purity = overlap / target_n
source_coverage = overlap / source_n
```

### 3.2 关系分层

```text
same_chain：
  target_purity 高（约 >=0.35）
  且目标核心行业/产业链与源相同
  → 补涨，排除

adjacent：
  target_purity 约 0.15–0.35
  有连接但不是同一链
  → 外溢候选池

cross：
  target_purity <0.15
  → 独立轮动，不是外溢
```

注意：

```text
target_purity 的数值带只是候选池
不等于 chain_role
```

必须再加：

```text
核心行业
产业链方向
龙头/角色重叠
历史联动
```

否则会出现：

```text
机器人 adjacent：
  轮胎轮毂 / 车身附件 / 综合乘用车
→ 误判同链

半导体 adjacent：
  消费电子 / AI手机
→ 误判同链

商业航天 adjacent：
  6G / 5G / 航母 / 国防军工
→ 范围过宽
```

### 3.3 首日/冷启动

```text
first_day_absolute：
  prior_launch_days 为空
  过去 5–10 日没有板块级联动

prior_launch_days 非空 → 二波，直接 reject
```

冷启动/边缘：

```text
early_broad：
  prior_limit_5d <= 1
  limit_today >= 3
  breadth_jump > 0.2
```

不要用 raw breadth：

```text
broad 普涨日所有概念 breadth 0.9+
raw breadth 没有区分度
```

要用：

```text
prior_breadth_5d
prior_limit_5d
breadth_jump
limit_today
```

### 3.4 其他过滤

```text
mainline_down    → 排除（B反阶段需单独放行）
generic/财务标签 → 排除
0 涨停/无量      → 降级
```

---

## 4. B agent

### 4.1 输入

- source
- A 的候选方向池
- 每个候选：
  - overlap / target_purity / source_coverage
  - first_day / prior_launch_days
  - early_broad / breadth_jump / limit_today
  - history_10d
  - 成员、ret5/ret20、excess5/20
  - 历史人气
  - 龙头候选

### 4.2 输出

```json
{
  "target": "",
  "validator": {
    "spillover_ok": true,
    "overlap_n": 0,
    "first_day_ok": true,
    "label": ""
  },
  "roles": {"leader": "", "wind": [], "mid": []},
  "picks": []
}
```

### 4.3 龙头规则

```text
先分核心段/配套段

核心段：
  终端、总体、总装、运营、平台、主设备
→ 可以当龙头

配套段：
  连接器、电源、结构件、轴承、材料、加工设备
→ 只能中军/风向，不能当龙头
```

“低位核心”：

```text
低位 + 题材核心段
不是
低位 + 好公司
```

选龙头：

```text
核心段
→ 低位
→ 辨识度
→ 带动
→ 不按涨幅/封单/首板
→ 不打板
```

示例：

```text
商业航天：
  600118 中国卫星 = 卫星总体 → 可当龙头
  002025 航天电器 = 连接器 → 中军，不是龙头
```

### 4.4 买不了龙头

```text
1. 买主板确认中军
2. 买 ETF
3. 不做
```

禁止：

```text
买不了龙头 → 随便买同题材主板票
```

### 4.5 同票去重

```text
symbol → primary theme
跨题材去重
和趋势 sleeve 去重
```

---

## 5. 仓位规则

仓位不由 agent 决定。

首日与确认分开：

```text
首日 first_linkage = true   → 0.5 probe
二波确认（theme_sleeve_add）→ 0.5 add
非首日 watch_strong         → 0.25
watch                        → 0.0
```

首日不要求次日上涨；次日回踩正常。
确认门只用于加仓，不是首日买入门。

agent 的：

```text
buy / hold / avoid / trim
size_multiplier
```

只记录，不进交易层。

原因：

```text
agent avoid → size_multiplier=0
→ selected 里出现 0 仓
→ 交易层 floor 100 股
→ 选中但计划仓位 0
```

---

## 6. 盘中链路

```text
09:45 / 10:30 / 11:00 / 13:30
→ theme_intraday_snapshot
→ A rolling
→ need_switch
→ Candidate Builder
→ B
→ select
→ trade（13:30–14:30）
```

已有组件：

```text
30/60 分钟信号
实时 breadth/涨停
龙头/板块相对强度
13:30–14:30 执行窗口
盘中风控
```

缺：

```text
theme_source_agent（A 生产任务）
A→B 触发
盘中 facts 定时快照
```

---

## 7. 10/28 回测

### A

```text
09:45 源强
10:30–11:00 可判 rebound
13:30 A 确认 rebound
need_switch=true
```

### B

```text
target: 商业航天
leader: 600118 中国卫星
```

### 买入

```text
09:45 600118 49.48
13:30 600118 51.20
10/29 收盘 55.90
```

收益：

```text
09:45 买 → +12.98%
13:30 买 → +9.18%
```

对比不走 A：

```text
买源 688017
10/29 -3.76%
```

结论：

```text
A 判 rebound 是必要的
A 判 healthy → B 不触发 → 买源亏损
```

---

## 8. 已知问题

1. 生产 `space` 仍是“航天装备”，不是“商业航天/卫星互联网”。
2. A 还没进生产，B 仍是离线。
3. same_chain 仍靠 YAML 硬编码，需 chain_role 数据化。
4. 历史人气/独立性不是 PIT。
5. 跨题材同票未去重。
6. 盘中 facts 还没定时任务。
7. open_times 原始值会把 1 分钱下探算炸板，需 strict。
8. 11/04 二段跳还没接 second_jump。

---

## 9. 下一步

```text
P0
1. theme_source_agent 生产化
2. A→B 触发
3. space 映射到商业航天/卫星互联网
4. 盘中 15 分钟任务
5. 同票去重

P1
6. chain_role 数据化
7. PIT 快照
8. second_jump playbook
9. 回测 10/23–11/04 全链路
```

---

## 10. 文件索引

```text
docs/wolf_spillover_guide.md
docs/wolf_leader_selection_blind.md
config/mainline/spillover_chain_filter.yaml
scripts/build_spillover_candidates.py
scripts/validate_spillover.py
scripts/validate_first_day.py
jobs/theme_intraday_snapshot.py
jobs/theme_intraday_series.py
jobs/theme_intraday_multi.py
jobs/theme_state_agent.py
jobs/theme_sleeve_select.py
jobs/theme_sleeve_trade.py
data/asof/20261028_b_snapshot.json
data/asof/20261028_a_intraday.json
data/intraday/20261028_multi_15m.json
```

---

## 11. 一句话

```text
A 判“源反抽/高潮，要不要切”
B 判“切到哪个相邻方向，买哪个核心龙头”
仓位由题材规则定，不由 agent 定
```
