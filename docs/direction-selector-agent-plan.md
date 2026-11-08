# 方向选择子 agent（L0.5）方案

> 问题：选股方案的 `L0 方向/板块（人给定）`，能不能换成“从主线取方向”，让 agent 去选？
> 参考：`docs/ai-agent-lessons-from-b-rebound.md`、`docs/stock-selector-agent-plan.md`、`docs/agent-time-windows.md`。
> 结论：**能，但不是让 agent 自由选板块**。正确做法是“主线机械排名 → 固定子方向宇宙 → agent 在宇宙内排序 → 自动一致性闸（无人确认）→ 再交给选股 agent”。

---

## 0. 结论

L0 拆成两层：

```
L0  主线（wolf_mainline_select 机械排名 + 自动一致性闸；无人确认）
      ↓
L0.5 子方向选择（本方案：固定宇宙 + 事实底稿 + 规则 + agent）
      ↓
L1/L2/L3 选股（只在 focus/watch 的方向里跑）
```

- **L0 不交给 agent 自由判断**：主线是“科技/AI”还是“医药/新能源”，由 `wolf_mainline_select.py` 的机械排名 + 自动一致性闸决定；agent 不在全市场自由选主线，人只做审计和异常否决（不参与日常确认）。
- **L0.5 交给 agent**：在科技主线的**固定子方向宇宙**里，判断谁是 `focus`（主做）、`watch`（观察）、`avoid`（回避），并区分 `trend`（趋势仓）和 `theme`（题材仓）。
- **L0.5 的输出是方向，不是个股**；个股交给 L1/L2/L3。
- **先 shadow，再 advisory**：方向层比个股层更依赖判断，至少 20-30 个判例后再考虑推送，永不自动下单。

---

## 1. 现有 L0 能复用什么

`backend/app/services/wolf_mainline_select.py` 已经是一个 **PIT 的主线排名器**：

- 13 个主题：AI/算力/科技、半导体/芯片、医药、新能源/电池、机器人/智能制造、汽车/智驾、消费/内需、农业、电力/公用、稳增长/基建、资源/周期、金融、军工/航天。
- 打分：`r5`（5 日超额）、`combo`（rs5 + rs20 + 涨停家数 + 站上 MA20 比例），横截面 z 标准化，top3。
- 实测：`combo` 的 top3 与他重合 80%，中位名次 2；`r5` 只有 49%。
- 开关：`WOLF_MAINLINE_SELECT`、`WOLF_MAINLINE_SCORE=combo`。
- 所有输入 PIT（只用当日及以前）。

**它的缺口**：13 个主题太粗。

- “半导体/芯片”里同时装着存储、设备、材料、先进封装、大芯；
- “机器人/智能制造”里同时装着人形机器人、工业机器人、机床；
- 他的原话是“科技主线内部软硬切换、设备/材料/整机/零部件轮动”，不是“半导体一个板块”。

所以 L0.5 要做的，是在 `wolf_mainline_select` 的 top3 主题之下，加一层**子方向宇宙**，把“半导体/芯片”拆成存储/设备/材料/封装，把“机器人”拆成人形机器人/工业机器人，把“AI/算力/科技”拆成国算/PCB/二线光/液冷/电源/AI软。

---

## 2. 狼大的方向选择规则（从原话提炼）

### 2.1 只做主线，主线内轮动

> “贯穿的主线就是 AI……只做主线内部切换轮动，分类无非就是 2 个大类，软件和硬件……从软件切换到硬件，甚至精确到硬件的整机，然后再买点材料和零部件。”（2025-02-20）

⇒ 方向选择不是“今天买哪个板块”，而是“主线内部哪一段在动”。

### 2.2 方向必须有大哥/核心，且核心绑定板块

> “核心标的……板块内总市值前 5；营收出现拐点、扭亏为盈或增速加速；核心活，板块活；核心死，板块死。”（2025-12-06）

> “机器人这波有了主心骨之后资金做得很不错……有个大哥宇树做主轴了。”（2026-10-20）

> “重点是有资金拿宇树做大哥……一个板块能不能走起来一定要有梯队，不是连板梯队，是板块内大哥二哥这样的梯队……一定要有个龙头风向。”（2026-10-23）

⇒ 没有大哥、没有梯队的方向，不配 `focus`。

### 2.3 大哥要强于板块，板块要强于大盘

> “这个票的走势一定要强于板块走势，板块同期要优于大盘走势。”（2025-12-06）

⇒ 方向打分第一层：`leader_rs > 0`、`sector_rs > 0`；大哥不行的方向，技术反弹也不做。

### 2.4 低位看逻辑，高位看量价

> “低位看逻辑，高位看量价。”（2026-07-31）

⇒ 方向在低位时，允许用产业逻辑/催化/业绩预期选；到高位后，只看量价、筹码、拥挤度。

### 2.5 筹码干净：低公募、低年内涨幅、低套牢、有主动资金

> 楼友问：“机器人筹码方向是不是因为**前面没有大型公募持仓，今年也没大涨，基本没有抛压**，同时有宇树做风向标？”
> 他回：“重点是有资金拿宇树做大哥……一定要有梯队……龙头风向。”（2026-10-23）

> “存储后面有一大堆公墓大科技拖累啊……机器人大哥先涨，其他哪怕不涨他也不会跌。”（2026-10-23）

⇒ 方向层的“筹码干净”= 公募拥挤低 + 年内涨幅低 + 套牢盘少 + 有主动资金 + 有大哥/梯队。机器人得分高，存储得分低（被公募大科技拖累）。

### 2.6 有量、有容量、有业绩

> “有活跃量能……没量的性价比不高。”（2025-06-17）

> “A 股存储标都是库存……只能做两长的存储扩产链条才有意义。”（2025-12-19）

⇒ 方向要有成交量/宽度，能容纳资金；纯故事、无量、无业绩的方向，只能 `watch`。

### 2.7 避开海外链/大芯/大光/拥挤

> “避开海外链……里面公墓太多，一旦公墓破位滚雪球出场，不可能靠自身停止。”（2026-07-28）

> “科技最后剩半导体材料和存储能有点剩余空间，避开大芯和大光。”（2026-09-02）

⇒ 方向层的硬排除：海外链、大芯（中芯国际）、大光（光模块龙头）、高位拥挤、无量。

### 2.8 两轨：趋势方向 + 题材方向

> “科技这类我仍然看好，下半年的 PCB、二线光、半导体材料。”（2026-10-21）

> “设备 ETF 逐渐换成 PCB、二线光、半导体材料。”（2026-10-21）

> “细分题材目前还是机器人。”（2026-10-23）

⇒ 他的方向不是一条线，而是**两轨**：
- `trend` 趋势仓：PCB、二线光、半导体材料、国算、液冷/电源；
- `theme` 题材仓：机器人、存储、AI 软、先进封装；
- 两轨分开打分、分开仓位、分开退出。

### 2.9 只做看得懂的确定钱

> “我只做我看得懂的确定的钱。”（2021-08-23）

⇒ 方向宇宙必须限定在他的能力圈（科技/AI + 少量周期），不开放全市场。

---

## 2.5 频率分层：主线按月、题材按日

> 2026-10-26 用户提出：漏掉的板块（资源/军工/金融/医药/基建）是“明确不做的题材”，不是主线；主线应按月度算，日频只能算题材（农业、机器人这种）。数据支持这个判断。

### 2.5.1 证据：日频 top1 是“题材排名”，不是“主线”

用 `docs/wolf-vs-system-compare.md` 的 109 天映射 + 2026 bars 复算：

| 指标 | 结果 |
|---|---|
| combo top1 | **27.5%（30/109）** |
| combo top3 | **83.5%（91/109）** |
| top1 miss 时我们的 top1 | **68/79 天是“新能源/电池”** |
| top1 miss 时他在做 | 半导体 39 天、AI/算力 38 天、资源 27 天、军工 13 天 |
| top3 也漏的 18 天 | 几乎全是**非科技题材**：资源/周期、军工、金融、医药、基建 |

⇒ 日频 combo 的 top1 被“新能源/电池”这种**小票多、涨停多**的题材粘住；它不是主线，是**题材轮动排名**。

### 2.5.2 月度主线：AI/科技 8/8

把主线宇宙限定为 `AI/算力/科技 + 半导体/芯片`，按**月度超额**排序：

| 月份 | 我们的月度主线 | 他的月度 top | 是否一致 |
|---|---|---|---|
| 2026-01 | 半导体/芯片 | 半导体/芯片 | ✅ |
| 2026-02 | 半导体/芯片 | 资源/周期、AI/算力 | ✅（AI 在 top2） |
| 2026-03 | AI/算力/科技 | 资源/周期、AI/算力 | ✅ |
| 2026-04 | 半导体/芯片 | AI/算力、半导体 | ✅ |
| 2026-05 | 半导体/芯片 | AI/算力、半导体 | ✅ |
| 2026-06 | 半导体/芯片 | 半导体、新能源、AI | ✅ |
| 2026-07 | AI/算力/科技 | AI/算力、半导体 | ✅ |
| 2026-08 | 半导体/芯片 | 半导体、AI/算力 | ✅ |

**8/8**：他的月度 top2 里每个月都有 AI/半导体；月度主线不需要像日频那样每天翻。

### 2.5.2b 月度细分方向（6 个科技主题）：top3 8/8，top1 7/8

把细分方向限定为 `AI/算力、半导体、机器人、汽车/智驾、军工/航天、新能源/电池`，按月度超额排序：

| 月份 | 我们的 top3 | 他的 top3 | top1 | top3 |
|---|---|---|---|---|
| 2026-01 | 半导体、AI、新能源 | 半导体、AI、新能源 | ✅ | ✅ |
| 2026-02 | 新能源、军工、机器人 | AI、军工、半导体 | ❌（top1 是新能源） | ✅ |
| 2026-03 | 新能源、汽车、AI | AI、新能源、军工 | ✅ | ✅ |
| 2026-04 | 半导体、AI、新能源 | AI、半导体、新能源 | ✅ | ✅ |
| 2026-05 | 半导体、机器人、新能源 | AI、半导体、军工 | ✅ | ✅ |
| 2026-06 | 半导体、机器人、军工 | 半导体、新能源、AI | ✅ | ✅ |
| 2026-07 | AI、汽车、新能源 | AI、半导体、新能源 | ✅ | ✅ |
| 2026-08 | 半导体、军工、AI | 半导体、AI、军工 | ✅ | ✅ |

**结论**：
- **月度细分方向 top3 = 8/8**，可以当候选池；
- **top1（宽松 top2）= 7/8**，2 月单押会错（新能源 vs AI/半导体），但 top3 兜住了；
- 所以“准确找到细分方向”应定义为 **top3 候选**，不是 top1 单押。

### 2.5.2c 日频细分方向：top1 42.6%、top3 73.4%

同样 6 个科技主题，按日频超额排序（94 个科技日）：
- top1 **42.6%**；
- top3 **73.4%**。

⇒ 日频只能做“题材排名/观察池”，不能单押；要单押必须叠加 leader/量能/催化 + 2 天迟滞。



### 2.5.3 三层分类

| 层级 | 频率 | 例子 | 作用 | 评价口径 |
|---|---|---|---|---|
| **主线** | 月/季 | AI/科技（AI/算力 + 半导体） | 决定“做不做科技”、总仓位 | 月度命中 |
| **方向/赛道** | 周/月 | 存储、机器人、PCB、二线光、材料、国算、液冷、AI 软 | 决定“做哪条线” | 周/月 top3 |
| **题材** | 日/周 | 资源/周期、农业、军工、金融、医药、基建、事件热点 | 短线轮动/小仓 | 日频，独立评价 |

- **主线不是“每天最强的主题”**，而是“这个月/这个季度能持续容纳资金的方向”。
- **机器人、存储是“方向/题材”，不是主线**；它们可以在主线内 `focus`，但不能替代“AI/科技”这条主线。
- **资源/农业/军工/金融/医药/基建是“题材”**；他明确不做，就不进主线评价，最多做“卫星题材仓”（小仓、短持）。

### 2.5.4 对 L0/L0.5 的修正

1. **L0-M 月度主线**：机械算，不需要日频 agent。宇宙 = AI/科技（AI/算力 + 半导体）；按月度超额 + 大哥/业绩排序；输出 `mainline = AI/算力` 或 `半导体/芯片`；**一个月内不因日频 top1 改变**，除非 regime 突变。
2. **L0-D 日频题材**：agent 只在“当月主线”的子方向里选 `focus/watch/avoid`（存储、机器人、PCB、二线光、材料、国算、液冷、AI 软）；日频，允许 2 天迟滞。
3. **卫星题材仓**：资源/农业/军工/金融/医药/基建，单独一个 sleeve，仓位 ≤10-15%，只做短差；**不计入主线准确率**，也不允许它把主线挤掉。
4. **评价分开**：
   - 主线：月度命中（当前 8/8）；
   - 方向：周/月 top3；
   - 题材：日频命中/收益，独立统计；
   - **不再用“日频 top1”评价主线**——那是把题材当主线。

### 2.5.5 边界

- 月度主线只在 2026（科技年）验证了 8 个月；2021-2022 这种非科技年，主线可能不是 AI/科技，需要把宇宙放宽到“新能源/医药/消费”等**耐用主线候选**，但仍按月度算。
- 月度主线宇宙要**语义约束**：只放“能持续 1-3 个月、能容纳资金”的方向；资源/农业/军工/金融/医药/基建默认是题材，不放进主线宇宙。
- 不要为了月度回测好看调权重；先用“月度超额 + 大哥/业绩”的简单规则，跑 12-24 个月再谈优化。

---

## 3. 子方向宇宙（固定，可维护）

`config/direction_selector/universe.yaml`。每个方向必须有：`sleeve`、`concept`、`benchmark`、`leader`、`ladder`、`notes`。

| 方向 | sleeve | 概念/ETF | 大哥 | 梯队 | 备注 |
|---|---|---|---|---|---|
| 存储芯片 | theme | 886042.TI / 半导体 ETF | 长鑫科技、兆易创新 | 澜起、佰维、德明利、江波龙、普冉、东芯 | 模组只做映射短差；设计/原厂优先 |
| 人形机器人 | theme | 886069.TI / 机器人 ETF 562500 | 宇树科技 | 兆威、绿的、三花、拓普、中大力德、长盛、五洲 | 筹码干净；大哥/梯队；Q3 密集 |
| 半导体设备 | trend | 884229.TI / 半导体设备 ETF | 北方华创、拓荆 | 中微、长川、中科飞测、华海清科 | 2026 下半年设备增速下降，降级 |
| 半导体材料 | trend | 884091.TI | 雅克科技、鼎龙股份 | 安集、江丰、有研新材 | 他 2026-10-21 明确看好 |
| PCB | trend | 885756.TI? / PCB 概念 | 胜宏科技 | 沪电、生益、深南、鹏鼎 | 他 2026-10-21 明确看好 |
| 二线光 | trend | 光通信概念 | 新易盛/天孚 | 太辰光、长飞、光迅 | 大光（中际旭创）回避 |
| 国算/算力 | trend | 算力概念 | 中科曙光/浪潮 | 紫光、中兴、润泽 | 他长期主线 |
| 液冷/电源 | trend | 液冷/电源概念 | 英维克 | 申菱、欧陆通、麦格米特 | 2026 液冷/电源 |
| AI 软 | theme | AI 应用概念 | 蓝色光标 | 金山办公、用友、科大讯飞 | 题材属性 |
| 先进封装 | theme | 先进封装概念 | 长电科技 | 通富、华天、甬矽 | 存储拉动 2 天后的延伸 |
| 大芯（中芯国际） | avoid | 688981.SH | — | — | 他 2026-09-02 明确避开 |
| 大光（光模块龙头） | avoid | 中际旭创/新易盛 | — | — | 海外链+公募拥挤 |
| 海外链 | avoid | CPO/海外映射 | — | — | 2026-07-28 明确避开 |

**维护规则**：
- 只增删方向，不改“打分逻辑”；每次增删要写 `universe_changelog.md`，并跑一遍已知判例。
- 每个方向必须有 `leader`（大哥）和至少 3 个 `ladder`（梯队），否则不能 `focus`。
- 方向宇宙不硬编码语料；语料只用于“这个方向他怎么看”的备注和验收标签。

---

## 4. 方向事实底稿（程序）

`jobs/wolf_direction_select_facts.py`，输出 `data/direction_selector/{as_of}/directions.json`。

### 4.1 字段

```json
{
  "as_of": "20261023",
  "mode": "post_close",
  "phase": "close",
  "mainline": {
    "source": "wolf_mainline_select",
    "top3": ["AI/算力/科技", "半导体/芯片", "机器人/智能制造"],
    "scores": {"AI/算力/科技": 1.82, "半导体/芯片": 1.41, "机器人/智能制造": 1.20}
  },
  "regime": {
    "index_wave": "下跌1浪/延伸浪",
    "index_pos": 0.31,
    "market_amount_ratio": 0.92,
    "note": "上证 3690 < 3764，P3 G3 空仓"
  },
  "directions": [
    {
      "name": "人形机器人",
      "sleeve": "theme",
      "concept": "886069.TI",
      "benchmark": "562500.SH",
      "stage": "rebound",
      "ret20": -0.034,
      "ret60": -0.12,
      "ytd": -0.079,
      "dd60": -0.18,
      "pos60": 0.41,
      "breadth_up_ratio": 0.875,
      "median_pct": 2.63,
      "amount_ratio": 1.46,
      "leader": {
        "code": "688836.SH", "name": "宇树科技", "pct": 20.0,
        "ret20": 0.24, "rs20": 0.274, "top_list_net_yi": 2.11,
        "fund": {"n_funds": null, "float_pct": null, "note": "次新，无公募数据"}
      },
      "ladder": [
        {"code": "003021.SZ", "name": "兆威机电", "ret20": 0.34, "rs20": 0.374, "fund_pct": 0.16},
        {"code": "688017.SH", "name": "绿的谐波", "ret20": -0.002, "rs20": 0.032, "fund_pct": 1.68},
        {"code": "002050.SZ", "name": "三花智控", "ret20": 0.016, "rs20": 0.050, "fund_pct": 1.52}
      ],
      "ladder_breadth": 0.72,
      "fund_crowding": {"n_funds_avg": 4.1, "float_pct_avg": 0.86, "max_pct": 1.68},
      "ytd_gain_median": -0.31,
      "overhead": 0.28,
      "earnings": {"q3_forecast_coverage": 0.18, "positive_ratio": 0.8, "report_window": "20261027-20261031"},
      "moneyflow_5d_yi": 12.2,
      "relative_strength": {"vs_tech_etf": 0.03, "vs_sh": 0.01},
      "crowd_space": {"crowd": 0.42, "space": 0.71},
      "catalysts": ["宇树上市", "特斯拉 Optimus 进展", "Q3 财报"],
      "risk_flags": ["Q3 未预告公司多", "10/23 单日普涨", "次新大哥无公募数据"],
      "data_warnings": ["公募持仓截至 2026-06-30"]
    }
  ]
}
```

### 4.2 计算口径

- 方向收益/宽度：**成分股等权**，不用 THS 指数（`ths_daily` 实测有滞后，2026-08-18 后无数据）；用 `stock_pool.db` / `ths_member` 取成分，`mkt_bars_daily` 算。
- benchmark：方向对应 ETF（机器人 562500、半导体 512480、芯片 159995、通信 515880 等）；没有 ETF 就用科技 ETF + 上证。
- `amount_ratio`：方向成分股当日成交额 / 20 日均额。
- `breadth_up_ratio`：当日上涨家数 / 有效家数。
- `ladder_breadth`：梯队中站上 MA20 的比例。
- `fund_crowding`：`data/fund_holdings/aggregate.json` + `data/crowding_pit/*`，按方向成分聚合；标 `stale_days`。
- `earnings`：`forecast` / `express` / `disclosure_date`，按方向成分统计。
- `moneyflow_5d`：`moneyflow` 的 lg+elg，按成分汇总。
- `crowd_space`：`data/rotation_crowding.json` / `rotation_quadrant_history_pit.json`。
- `catalysts`：人工维护的事件日历（政策、发布会、财报、海外映射），只作为事实，不给结论。
- `data_warnings`：数据滞后/缺失，必须显式。

### 4.3 硬规则

- 方向数据必须 PIT；`as_of` 之后的数据一律不进。
- 没有数据的方向写 `null`，不写 0；agent 必须说“缺”，不能填。
- 不给“建议/评分/重点”，只给事实。

---

## 5. 方向打分与路由

### 5.1 程序：机械排名（只用于排序，不给结论）

参考 `wolf_mainline_select.score_day` 的口径，对子方向算：

```text
score = 0.35 * z(r5) + 0.20 * z(rs20) + 0.15 * z(breadth) + 0.15 * z(leader_rs) + 0.15 * z(ladder_breadth)
```

- `r5`：5 日超额（方向等权 − 全市场等权）。
- `rs20`：20 日超额。
- `breadth`：站上 MA20 比例 / 当日上涨比例。
- `leader_rs`：大哥相对方向的超额。
- `ladder_breadth`：梯队强度。
- 权重是**路由初值**，不是结论；每次调权重要跑已知判例，不能为回测好看加规则。
- 机械排名输出 `rank`，agent 可以不同意，但必须解释“为什么与机械排名不同”。

### 5.2 规则路由

先定 `regime`，再选分支：

| regime | 方向偏好 | 仓位 |
|---|---|---|
| 主升/确认 | trend 方向优先，theme 跟随 | 70-100% |
| 高位震荡 | theme 短打 + 被动止盈 | 30-50% |
| 下跌浪/破位 | 只 watch，不 focus；现金/ETF | 0-30% |
| 反弹初段 | theme 主动联动 + trend 超跌 | 10-30% |

再按规则：

- **D1 只做主线内**：方向必须在 universe，且属于 top3 主线主题。
- **D2 大哥/梯队**：`leader` 存在且 `leader_rs > 0`；`ladder_breadth ≥ 0.5`；否则 `watch`。
- **D3 相对强度**：`leader_rs > 0` 且 `sector_rs > 0`；否则 `avoid`。
- **D4 量能/宽度**：`amount_ratio ≥ 1.0` 且 `breadth_up_ratio ≥ 0.5`；否则 `watch`。
- **D5 筹码干净**：`fund_crowding.float_pct_avg ≤ 1.5%` 且 `ytd_gain_median ≤ 0` 且 `overhead ≤ 0.5`；否则扣分/降级。
- **D6 位置/段位**：低位（`pos60 < 0.5`）可用逻辑/催化；高位（`pos60 > 0.8`）只看量价/拥挤。
- **D7 业绩/催化**：有 Q3 预告/政策/产业催化；纯故事无量 → `watch`。
- **D8 排除**：海外链、大芯、大光、高位拥挤、无量 → `avoid`。
- **D9 容量**：方向至少 5 只有效成分、日均成交额 ≥ 50 亿；否则不 `focus`。
- **D10 两轨**：`trend` 与 `theme` 分开；最多 2 个 `focus`（1 trend + 1 theme），总方向 ≤3。

### 5.3 输出映射

| 规则结果 | verdict |
|---|---|
| D1-D4 全过 + D5 过 + D6 低位 + D7 有催化 | `focus` |
| D1-D4 过，但 D5 拥挤 / D6 高位 / D7 无业绩 | `watch` |
| D3 或 D8 失败 | `avoid` |
| 数据缺失无法判断 | `unknown`（不允许默认 focus） |

---

## 6. Agent prompt 与输出

### 6.1 system prompt

```text
你是狼大交易体系的“方向选择子 agent”。你只在给定的科技子方向宇宙里排序，不选主线、不选个股、不下单。

硬规则：
1. 先定 regime（主升/高位/下跌/反弹），再选方向分支；regime 未定，不许给 focus。
2. 只用 facts 中存在的字段；缺数据就说“缺”，不要用次优数据填。
3. 方向必须有大哥 + 梯队；大哥不强于方向、方向不强于大盘，不能 focus。
4. 筹码干净：公募拥挤低、年内涨幅低、套牢盘少、有主动资金。
5. 区分 trend（趋势仓）与 theme（题材仓），最多 2 个 focus，总方向 ≤3。
6. 系统闸门 allow_new=false 时，只能给 watch，不能给 focus。
7. 不打分、不给胜率；每条判断带日期+数字；必须给反证条件。
8. 输出严格 JSON，不要解释文字；数据冲突写进 data_conflicts。
```

### 6.2 user prompt

```text
主线（机械排名 top3）：{mainline_top3}
as_of：{as_of}
regime facts：{regime_json}
方向 facts：{directions_json}
判据：{rules_json}

问题：在科技主线的子方向里，哪些 focus（trend/theme）、哪些 watch、哪些 avoid？
输出 JSON schema：{schema}
```

### 6.3 输出 schema

```json
{
  "as_of": "20261023",
  "mainline": {"top3": ["AI/算力/科技", "半导体/芯片", "机器人/智能制造"]},
  "regime": {"stage": "下跌1浪/延伸浪", "index_pos": 0.31, "allow_new": false},
  "focus": [
    {
      "direction": "人形机器人",
      "sleeve": "theme",
      "verdict": "focus",
      "why": [
        "大哥宇树+20%，龙虎榜净买2.11亿（20261023）",
        "板块87.5%上涨，量1.46x，梯队兆威+34%/绿的+12%",
        "公募低、年内-7.9%，筹码干净",
        "10/23主动联动，资金第一次做板块"
      ],
      "leader": "688836.SH 宇树科技",
      "ladder": ["003021.SZ", "688017.SH", "002050.SZ"],
      "horizon": "3-10日（题材）",
      "counter_evidence": [
        "若宇树跌破MA5/量能<0.8x，题材结束",
        "若Q3机器人公司大面积不及预期，降级watch"
      ]
    },
    {
      "direction": "PCB/二线光/半导体材料",
      "sleeve": "trend",
      "verdict": "focus",
      "why": ["他2026-10-21明确看好", "设备ETF→PCB/二线光/材料"],
      "leader": "胜宏科技/新易盛/雅克科技",
      "horizon": "1-3个月（趋势）",
      "counter_evidence": ["若大光破位/海外链转弱，降级"]
    }
  ],
  "watch": [
    {
      "direction": "存储芯片",
      "sleeve": "theme",
      "verdict": "watch",
      "why": [
        "Q3业绩强（德明利/长鑫/复旦）",
        "但公募拥挤：兆易12只/2.8%、澜起14/4.79%",
        "他要带整个半导体，难度大"
      ],
      "trigger": "长鑫/兆易带量过前高 + 存储板块缩转放 + 公募不再卖"
    }
  ],
  "avoid": [
    {"direction": "大芯/大光/海外链", "reason": "2026-09-02明确避开；公募滚雪球"}
  ],
  "allocation": {"trend": 0.5, "theme": 0.2, "cash": 0.3},
  "rotation": {"reduce": ["设备ETF"], "add": ["PCB", "二线光", "半导体材料"]},
  "data_conflicts": [
    "机器人10/23单日普涨，但20日仍-3.4%；按D3是反弹不是趋势"
  ],
  "system_gate": {"allow_new": false, "effect": "focus 降级为 watch"}
}
```

---

## 7. 工作示例：2026-10-23 机器人 vs 存储

| 维度 | 人形机器人 | 存储芯片 |
|---|---|---|
| 大哥 | 宇树 +20%，龙虎榜 +2.11 亿 | 长鑫 +7.93%，兆易 +5.51% |
| 梯队 | 兆威 +10%、绿的 +12%、三花 +6.7%、拓普 +5.7% | 澜起 +2.8%、佰维 +3.4%、德明利 +3.2% |
| 宽度/量 | 87.5% 上涨，量 1.46x | 87.5% 上涨，量 1.46x |
| 筹码 | 公募低、年内 -7.9%、套牢少 | 公募拥挤（兆易 12/2.8%、澜起 14/4.79%） |
| 业绩 | Q3 分化（震裕预增 58-70%） | Q3 强（德明利扭亏、长鑫 1310 亿、复旦预增） |
| 他 10/23 原话 | “细分题材目前还是机器人”“有大哥、有梯队” | “存储后面有一大堆公墓大科技拖累” |
| 结论 | **focus（theme）** | **watch（theme，只做短差）** |

再叠加他 10/21 的“下半年 PCB、二线光、半导体材料”，L0.5 的输出应是：

- `focus`：机器人（theme）+ PCB/二线光/半导体材料（trend）；
- `watch`：存储芯片（等长鑫/兆易带量过前高、公募不再卖）；
- `avoid`：大芯、大光、海外链；
- 仓位：trend 50% / theme 20% / cash 30%（当前下跌浪，P3 G3 空仓时全部降为 watch）。

---

## 8. 验证

### 8.1 判例集（先 15-20 个）

| 日期 | 期望 |
|---|---|
| 2026-10-23 | 机器人 focus(theme)、PCB/二线光/材料 focus(trend)、存储 watch、大芯/大光 avoid |
| 2026-10-21 | 设备 ETF→PCB/二线光/材料；机器人 active |
| 2026-10-20 | 机器人第一次主动联动；存储/机器人两个主动方向 |
| 2026-09-02 | 材料/存储；避开大芯/大光 |
| 2026-07 存储崩盘 | 存储 avoid/减仓；公募滚雪球 |
| 2026-05-09 | 存储映射 focus(theme)，但“外围转弱就跑” |
| 2025-11-21 | 存储模组 avoid；设备/材料 watch |
| 2025-05-29 | 机器人筹码干净 → 二买 focus(theme) |
| 2026-06-30 | 主升末段 → defense/reduce |
| 2021-2022 若干 | 换年份/换板块留出 |

### 8.2 指标（不是胜率）

- `focus` 方向后 5 日相对科技 ETF 的超额（命中/不命中）。
- `false focus`：focus 但跑输的比例。
- `avoid` 正确率：avoid 方向后 5 日是否跑输。
- 轮动时点：focus 切换比实际行情早/晚几天。
- 数据冲突发现数、反证质量、稳定性（同 facts 两次运行一致）。
- 误杀：他明确做过的方向被 avoid 的次数。

### 8.3 盲测

- 新建 subagent，不给日期/结论，只给 mainline + facts + rules。
- 先跑 10/23、10/21、09/02、07 崩盘、05/09 映射五个 case，再扩到 20 个。
- 记录失败日志 `docs/direction-selector-failure-log.md`，每次修正来自具体失败。

---

## 9. 工程落地

### 9.1 文件

```
config/direction_selector/
  universe.yaml
  rules.yaml
  output_schema.json
jobs/
  wolf_direction_select_facts.py
  wolf_direction_select_agent.py
  wolf_direction_select_validate.py
  wolf_direction_select_outcome.py
data/direction_selector/{as_of}/
  directions.json
  decision.json
  validation.json
docs/
  direction-selector-agent-plan.md   # 本方案
  direction-selector-failure-log.md
  direction-selector-universe-changelog.md
```

### 9.2 调度

| 任务 | 时间 | 说明 |
|---|---|---|
| L0 主线 | 15:00 | `wolf_mainline_select`（已有） |
| L0.5 facts | 15:05 | 子方向事实 |
| L0.5 agent | 15:10 | `/chat` 或 DSH subagent |
| L0.5 validate | 15:15 | schema/规则/闸门 |
| L1 选股 | 15:20 | 只在 focus/watch 方向跑 |
| 推送 | 15:25 | 方向 + Top3 个股 + 条件 |
| 结果 | T+1/3/5 | 方向/个股分别跟踪 |

### 9.3 PG 表

```sql
create table if not exists direction_selector_runs (
  id bigserial primary key,
  as_of date not null,
  mainline jsonb not null,
  regime jsonb not null,
  decision jsonb not null,
  validation jsonb not null,
  system_gate jsonb not null,
  created_at timestamptz default now(),
  unique (as_of, mainline->>'hash', decision->>'hash')
);

create table if not exists direction_selector_outcomes (
  id bigserial primary key,
  run_id bigint references direction_selector_runs(id),
  direction text not null,
  verdict text not null,
  t1_rel numeric, t3_rel numeric, t5_rel numeric,
  max_dd numeric,
  recorded_at timestamptz default now()
);
```

### 9.4 分期

| 阶段 | 工期 | 产出 | 验收 |
|---|---|---|---|
| P0 方向宇宙/规则 | 1 天 | `universe.yaml` + `rules.yaml` | 15 个方向有 leader/ladder；规则有 check |
| P1 方向 facts | 1-2 天 | `wolf_direction_select_facts.py` | 10/23/10/21 两份 facts 可复现 |
| P2 影子 agent | 1-2 天 | `wolf_direction_select_agent.py` | 10 个判例盲测；不推送 |
| P3 咨询推送 | 1 天 | QQ 方向 + 个股 | 自动一致性闸 + 审计日志；无变化静默 |
| P4 结果跟踪 | 持续 | T+1/3/5 + 失败日志 | 20-30 case 后再评估是否接 gate |

---

## 10. 风险与不做什么

1. **不让 agent 自由选主线**：主线由 `wolf_mainline_select` + 自动一致性闸；agent 只在子方向宇宙里选；人只审计。
2. **不让 agent 自由扩方向**：universe 固定，新增方向要人工 + 判例测试。
3. **不把描述当筛选器**：不用“相位表”全历史扫描；只用当日 facts + 规则。
4. **不让 agent 自己取数/写库**：程序算 facts，agent 只判断。
5. **不混窗口/单位**：`mode/phase`、`_yi/_pct/ratio`、`stale_days`。
6. **不打分/不问胜率**：用 focus 命中、false focus、avoid 正确率、轮动时点、数据冲突。
7. **不自动交易**：方向 focus 可以自动，但执行层仍受 P2/P3、仓位上限、单方向上限约束；人保留 kill switch 和异常否决。
8. **数据边界**：公募拥挤季度/PIT；THS 指数可能滞后，用成分股等权；宇树次新无公募数据；Q3 预告只覆盖部分；龙虎榜只在异动日有；拿不到盘口/GJD。
9. **先 shadow**：方向层比个股层更依赖判断，至少 20-30 个判例后再 advisory；`docs/ai-asof-trial.md` 的结论仍然是“AI 只做裁判/shadow，不接 gate”。

---

## 11. 自动模式（无人工确认）

> 前提：主线判定已经验证过（`combo` top3 与他重合 80%、top1 54%、方向后 5 日超额 +0.91%；PIT 口径）。  
> 所以 **L0 主线不需要人确认**；L0.5 方向也可以自动，但要把“人确认”替换成**自动一致性闸 + 迟滞 + 不确定性输出**，否则会天天翻方向。

### 11.1 自动模式分级

| 层 | 模式 | 说明 |
|---|---|---|
| L0 主线 | `auto` | `wolf_mainline_select`（combo）+ 自动一致性闸；无人确认 |
| L0.5 方向 | `auto` | agent 给 focus/watch/avoid；自动闸替代人确认 |
| L1 选股 | `shadow` 先跑 | 20-30 个判例后再 advisory |
| 执行 | `paper` 先跑 | 真钱仍受 P2/P3、仓位上限、kill switch |

### 11.2 L0 主线：`mainline_confirmed` 自动判定

输入：`wolf_mainline_select` 的 `mainline`、`second`、`r5_raw`、`diag`、`pool`、`rank_all`、`dropped_by_r5`，以及 `main_line_state.json` 的历史。

```text
mainline_confirmed = 
  mainline in pool
  and r5_raw[mainline] > 0
  and gap(mainline, second) >= gap_min
  and mainline 连续 N 天在 top2
  and data_coverage >= 0.90
  and mainline not in dropped_by_r5
  and stale_days <= 1
```

- `gap_min` 初值 **0.15**（z/评分单位），用 20 个历史日校准；不拍脑袋定死。
- `N` 初值 **2 天**；主升期可降到 1 天，下跌期保持 2 天。
- 不满足 → `mainline_uncertain`：**保留上一版 mainline**（`main_line_state.json`），L0.5 只输出 `watch/avoid`，不产生新 `focus`。
- 数据缺失/过期 → `mainline_uncertain` + `data_warning`，不用昨日数据冒充今日。

### 11.3 L0.5 方向：`direction_confirmed` 自动判定

```text
direction_focus = 
  mainline_confirmed
  and regime in [rebound, uptrend]        # 下跌/防御期只 watch
  and D1..D4 pass
  and D5 pass or soft_penalty_ok
  and D7 has_catalyst_or_earnings
  and direction 连续 2 天在 top3
  and system_gate.allow_new == true
```

- `focus` 最多 **2 个**：1 个 `trend` + 1 个 `theme`；总方向 ≤3。
- `focus` 进入要 **2 天确认**；退出要 **2 天不在 top3** 或硬规则失败；避免单日 flip-flop。
- `regime` 下跌/防御 → `focus` 全部降为 `watch`，只保留 `avoid` 清单。
- `P2/P3` 硬闸 → `focus` 降为 `watch`；不新开底仓。
- `data_warnings` 关键项（覆盖率 <80%、基准缺失、stale >5 天）→ `watch`/`unknown`，不 `focus`。
- `kill switch`：focus 篮子 2 日回撤 >8%，或相对科技 ETF 超额 < -5% → 自动降级 + 告警。

### 11.4 状态机

```text
mainline_confirmed?
├─ no  → 保留上一版 mainline；L0.5 只 watch/avoid；告警
└─ yes → regime?
    ├─ down/defensive → focus 全部 watch；只留 avoid + 观察池
    ├─ rebound/up     → D1..D10 → focus/watch/avoid
    └─ uncertain      → 只 watch；不新开

focus 变更?
├─ 新 focus：连续 2 天 top3 + 硬规则通过 + 闸门允许 → 进入
├─ 旧 focus：连续 2 天不在 top3，或硬规则失败 → 退出
└─ 单日异动：只进 watch，不进 focus

P2/P3 blocked?
└─ focus → watch；theme T 仓也只在 allow_T 时做
```

### 11.5 自动模式的配置（`config/direction_selector/auto.yaml`）

```yaml
auto_mode:
  l0_mainline: auto        # wolf_mainline_select combo
  l05_direction: auto      # agent + 自动一致性闸
  l1_stock: shadow         # 20-30 case 后改 advisory
  execution: paper         # 真钱需 P2/P3 + kill switch

mainline_guard:
  min_top1_days: 2
  min_gap: 0.15            # 先用 20 日校准
  min_coverage: 0.90
  max_stale_days: 1
  on_uncertain: keep_previous_watch

direction_guard:
  max_focus: 2
  max_directions: 3
  max_theme_focus: 1
  max_trend_focus: 1
  min_focus_days: 2
  exit_days: 2
  on_regime_down: watch_only
  on_gate_blocked: watch_only
  on_data_warning: watch_only
  kill_switch_drawdown: 0.08
  kill_switch_rs: -0.05

audit:
  log_facts_hash: true
  log_mainline_hash: true
  log_decision: true
  log_validation: true
  log_system_gate: true
  weekly_failure_review: true
```

### 11.6 人保留什么

不是“人确认”，而是：

- **审计**：每周看一次 `direction_selector_runs` 和失败日志。
- **异常否决**：数据源故障、黑天鹅、监管事件、明显矛盾的 `data_conflicts`，人可以一票否决。
- **kill switch**：一键停止自动 `focus`/下单，回到 `watch`。
- **规则变更**：新增/修改方向或规则，必须人审 + 判例测试。

### 11.7 上线条件（无人工确认前必须满足）

1. L0 主线：用现有 `combo` 口径跑 2-4 周 **no-human shadow**，与人工/历史判例对比，`focus` 命中率不下降。
2. L0.5 方向：15-20 个判例盲测；误杀（他做过的方向被 avoid）≤1；`false focus` 可解释。
3. 失败注入：缺数据、gap 过小、regime 翻转、P2/P3 拦截、重复/过期数据，每种都要有正确降级。
4. 回滚开关：`WOLF_MAINLINE_SELECT` / `auto_mode.l05_direction=shadow` 可一键回退。

---

## 12. 一句话

**L0 可以从“人给定”换成“从主线取”，而且主线层可以完全自动：`wolf_mainline_select` 出主线 top3 → 自动一致性闸（top1 稳定、gap、覆盖率、PIT）→ 固定科技子方向宇宙 → 程序算方向 facts → agent 给 focus/watch/avoid → 自动迟滞/闸门/kill switch → 选股 agent 只跑 focus/watch。** 人不再做日常确认，只做审计、异常否决和规则变更；执行层仍受 P2/P3、仓位上限和 kill switch 约束。
