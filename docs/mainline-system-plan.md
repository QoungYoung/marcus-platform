# 主线系统整体规划（L0 主线 → L1 赛道 → L2 细分 → L3 个股）

> 目标：把“这个月做不做科技、做哪条赛道、做哪条细分、买哪只票”拆成四层可复现的判断。
> 参考：`docs/direction-selector-agent-plan.md`、`docs/stock-selector-agent-plan.md`、`docs/ai-agent-lessons-from-b-rebound.md`、`docs/wolf-vs-system-compare.md`。
> 原则：**主线按月、赛道按月/周、细分按周/日、个股按日；程序给事实、规则给分支、agent 给判断、自动闸给否决、人只做审计。**

---

## 0. 一句话

**主线系统不是“每天选最强板块”，而是四层金字塔：**

```
L0 主线（月/季）  AI/科技                     —— 决定做不做科技
L1 赛道（月/周）  半导体 / 元件 / 通信设备 / 电池 / 军工 … —— 决定做哪条大赛道
L2 细分（周/日）  存储 / CPO / PCB / 设备 / 材料 / 机器人执行器 … —— 决定做哪条线
L3 个股（日）     兆易 / 宇树 / 三花 / 胜宏 …  —— 决定买哪只、什么价、多少仓
```

**关键结论（已验证）**：

- 月频主线 AI/科技：**8/8**；
- 月频科技细分（6 主题）：top3 **8/8**，top1 **7/8**；
- 日频细分：top1 **42.6%**、top3 **73.4%**，只能做观察池；
- 日频 13 主题 top1 **27.5%**，是**题材排名**，不是主线。

---

## 1. 四层金字塔：职责、频率、数据、谁做

| 层 | 频率 | 回答的问题 | 数据 | 谁做 | 输出 |
|---|---|---|---|---|---|
| **L0 主线** | 月/季 | 这个月做不做 AI/科技？总仓多少？ | `wolf_mainline_select`、宏观/regime、月度超额 | 机械 + 人审计 | `mainline`、`mainline_strength`、`regime` |
| **L1 赛道** | 月/周 | 半导体、元件、通信设备、电池、军工，做哪条？ | 申万 L2、`sw_daily`、`index_member_all` | 机械评分 + agent 复核 | `track_focus/watch/avoid` |
| **L2 细分** | 周/日 | 存储、CPO、PCB、设备、材料、机器人执行器，做哪条？ | 申万 L3、中证主题、东财概念 | agent | `theme_focus/watch/avoid` |
| **L3 个股** | 日 | 买哪只、什么价、多少仓、何时走？ | bars、财务、资金、拥挤、公告 | agent + P2/P3 | `probe/wait/watch/reject` |

**硬规则**：

- L0 **不按日频改**；L1 按周/月；L2 按周/日；L3 按日。
- L2/L3 只能在 L0/L1 的 `focus/watch` 里跑；`avoid` 直接排除。
- 任何一层都不允许“日频 top1 直接改主线”。

---

## 2. L0 主线：月频，AI/科技

### 2.1 主线宇宙

默认：`AI/科技 = AI/算力 + 半导体/芯片`。

备用（非科技年）：`新能源/电池`、`医药`、`消费/内需`，但只在月度层面切换，且需要连续 2 个月确认。

**不放进主线宇宙**：资源/周期、农业、军工、金融、医药（除非升级为年度主线）、基建。它们是**卫星题材**，不是主线。

### 2.2 主线评分

```text
mainline_score =
  0.40 * z(monthly_excess)       # 月度相对全市场等权
+ 0.20 * z(breadth_ma20)         # 成分站上 MA20 比例
+ 0.20 * z(leader_rs)            # 大哥相对主线的超额
+ 0.20 * z(earnings_inflection)  # 营收/利润拐点
```

- 月度算一次；月初 + regime 突变时重算。
- 权重只是初值；先跑 12-24 个月，不为了回测好看调参。

### 2.3 主线状态

```text
mainline_confirmed =
  mainline in [AI/算力, 半导体/芯片]
  and monthly_excess > 0
  and breadth_ma20 >= 0.5
  and leader_rs > 0
  and data_coverage >= 0.9
  and 连续 2 个月 top2
```

- `confirmed`：允许 L1/L2 产生 `focus`。
- `uncertain`：保留上一版主线，L1/L2 只出 `watch/avoid`，不新开。
- `data_warning`：数据缺失/过期 → `uncertain`，不用昨日数据冒充。

### 2.4 主线输出

```json
{
  "as_of": "20261031",
  "freq": "monthly",
  "mainline": "AI/科技",
  "sub_mainline": "半导体/芯片",
  "strength": 0.82,
  "regime": "下跌1浪/反弹",
  "confirmed": true,
  "next_review": "20261102",
  "evidence": ["月度超额 +3.1%", "breadth 0.58", "大哥 兆易/长鑫"],
  "counter_evidence": ["若下月 breadth <0.4 或 leader_rs <0，降级 uncertain"]
}
```

---

## 3. L1 赛道：月/周，申万 L2

### 3.1 赛道宇宙（10-15 个）

| 赛道 | 申万 L2 | 说明 |
|---|---|---|
| 半导体 | 801081.SI | 存储/设备/材料/设计/封测的父级 |
| 元件 | 270200 | PCB/被动元件 |
| 光学光电子 | 270300 | 光学元件/面板 |
| 通信设备 | 730200 | 光模块/CPO/通信传输 |
| 计算机设备 | 710100? | 服务器/算力硬件 |
| 电池 | 630700? | 新能源电池 |
| 光伏设备 | 630300 | 光伏 |
| 军工 | 801740? | 军工/航天 |
| 医药 | 370000? | 创新药/器械 |
| 机器人/自动化 | 机械设备下的自动化设备 | 工业机器人/自动化 |

> 代码以 `index_classify(level='L2')` 为准；上表只给方向，落地时用真实 `index_code`。

### 3.2 赛道评分

```text
track_score =
  0.35 * z(monthly_excess)
+ 0.20 * z(breadth_ma20)
+ 0.15 * z(leader_rs)
+ 0.15 * z(amount_ratio)
+ 0.15 * z(earnings_inflection)
```

- 月频主排序；周频做确认/降级。
- `leader` 用赛道内市值前 5 + 营收拐点 + 辨识度，不用东财 `leading` 当日领涨股。

### 3.3 赛道输出

```json
{
  "as_of": "20261031",
  "mainline": "AI/科技",
  "focus": [
    {"track": "半导体/芯片", "code": "801081.SI", "score": 0.81,
     "why": ["月度超额 +4.2%", "breadth 0.61", "大哥 兆易/长鑫"]},
    {"track": "通信设备", "code": "730200.SI", "score": 0.63,
     "why": ["光通信 931723 +3.8%", "CPO 概念 83/15"]}
  ],
  "watch": [{"track": "元件", "code": "270200.SI", "reason": "PCB 强但广度不够"}],
  "avoid": [{"track": "资源/周期", "reason": "非主线题材，不做"}]
}
```

---

## 4. L2 细分：周/日，申万 L3 + 中证主题 + 东财概念

### 4.1 细分宇宙（三层来源）

| 来源 | 例子 | 用途 |
|---|---|---|
| 申万 L3 | 集成电路 850811、半导体材料 850813、印制电路板 850822、通信传输设备 851013 | 行业级细分，PIT 成分 |
| 中证主题 | 存储芯片臻选 932580、机器人 H30590、PCB 932666、半导体材料设备 931743、芯片设计 932040、人工智能 931071、算力 931656/931657、光通信 931723 | 官方主题，成分+权重 |
| 东财概念 | 存储芯片 BK1137、CPO 概念 BK1128、PCB BK0877、人形机器人 BK1184、机器人执行器 BK1145 | 日频宽度/领涨/成交额 |

**分级规则**：

- **PCB**：SW L3 850822（行业级）+ CSI 932666 + 东财 BK0877 → L2 细分（偏行业）。
- **存储芯片**：CSI 932580 + 东财 BK1137；SW L3 父级 850811 集成电路 → L2 细分（偏主题）。
- **CPO**：东财 BK1128；无 SW L3/无 CSI 指数 → L2 **技术路线题材**，父赛道 通信设备/光通信。
- **人形机器人 / 机器人执行器**：东财 BK1184/BK1145；CSI H30590 → L2 细分（偏题材）。
- **半导体设备/材料/设计/封测**：SW L3 + CSI 931743/932040 → L2 细分（偏行业）。

### 4.2 细分评分

```text
theme_score =
  0.30 * z(ret20)
+ 0.20 * z(breadth_up_ratio)
+ 0.20 * z(leader_rs)
+ 0.15 * z(ladder_breadth)
+ 0.15 * z(amount_ratio)
- 0.10 * crowding_penalty
```

- 周频主排序；日频只做 `watch`/触发。
- `leader` 用“市值前 5 + 营收拐点 + 绑定板块”，不是东财 `leading`。
- `crowding_penalty`：公募 n_funds/float 高 → 扣分；不是硬删。

### 4.3 细分输出

```json
{
  "as_of": "20261023",
  "mainline": "AI/科技",
  "track": "半导体/芯片",
  "focus": [
    {"theme": "存储芯片", "source": "CSI 932580 / 东财 BK1137",
     "why": ["Q3 业绩强", "但公募拥挤，只做短差"], "verdict": "watch"},
    {"theme": "PCB", "source": "SW 850822 / CSI 932666",
     "why": ["AI 服务器放量", "广度 161/42"], "verdict": "focus"}
  ],
  "watch": [{"theme": "CPO", "source": "东财 BK1128", "reason": "技术路线，等海外映射"}],
  "avoid": [{"theme": "资源/周期", "reason": "卫星题材，不做"}]
}
```

---

## 5. L3 个股：日频，复用选股 agent

- 只在 L2 的 `focus/watch` 里跑。
- 事实底稿 + 规则 + agent + P2/P3，见 `docs/stock-selector-agent-plan.md`。
- 输出 `probe/wait/watch/reject`；`P2/P3` 禁止时只能 `watch`。
- 单票 ≤15%，总仓 ≤70%，主题仓 ≤20%，卫星题材 ≤10-15%。

---

## 6. 数据层：API 与口径

| 数据 | API | 用途 | 备注 |
|---|---|---|---|
| 申万 L1/L2/L3 | `index_classify(level='L1/L2/L3')` | 行业树 | 28/104/227 |
| 申万成分 | `index_member_all` | PIT 成员（in_date/out_date） | 5920 行 |
| 申万行情 | `sw_daily` | 行业日线/成交额/PE/PB | 按 ts_code |
| 中证指数列表 | `index_basic(market='CSI')` | 主题指数 | 9057 个 |
| 中证成分/权重 | `index_weight(index_code, trade_date)` | 主题成分+权重 | 按月/季 |
| 中证行情 | `index_daily(ts_code)` | 主题日线 | 新指数历史短 |
| 东财概念列表 | `dc_index(trade_date)` | 1043 个概念、宽度、领涨 | 当日 |
| 东财成分 | `dc_member(ts_code, trade_date)` | 概念成分 | 当前成员，非 PIT |
| 东财行情 | `dc_daily(ts_code, start, end)` | 概念日线 | 可回测 |
| 同花顺概念 | `ths_index/ths_member/ths_daily` | 交叉验证 | 部分指数滞后 |
| 个股行情 | `daily` / `pro_bar` | 个股日线 | datahubco 快 |
| 财务/公告 | `forecast/express/disclosure_date` | 业绩/事件 | Q3 窗口 |
| 拥挤 | `data/fund_holdings/aggregate.json`、`crowding_pit` | 公募拥挤 | 季度/PIT |
| 系统闸门 | P2/P3、`check_entry_filters` | 仓位/风控 | 只读 |

**口径守卫**：

- 所有数据带 `as_of` / `trade_date` / `stale_days`。
- 金额 `_yi`、比例 `_pct`、倍数 `ratio` 写进字段名。
- 成员 PIT：SW 用 `in_date/out_date`；CSI 用 `index_weight(trade_date)`；东财标 `membership_pit=false`。
- 不复用“未来数据”；不用昨日数据冒充今日。

---

## 7. 决策层：仓位、轮动、卫星

### 7.1 项目级 caps（不定义总仓位）

总仓位/现金由**现有仓位函数**决定；本系统只给每个 track/theme 加项目级 caps，供现有函数读取：

| 项目 | caps | 说明 |
|---|---|---|
| 主线 | `max_weight 0.70`、`max_tracks 3`、`max_themes 3`、`max_names 6` | 主线 sleeve 上限，**不是总仓位** |
| 赛道 | `max_weight 0.10-0.35`、`max_names 2-3` | 按 YAML |
| 细分 | `max_weight 0.08-0.20`、`max_names 1-2` | 按 YAML |
| 卫星 | `max_weight ≤0.10`、`allow_base false` | 只做短差 |
| 单票 | `max_single_stock 0.08-0.15` | 按 YAML |
| 试探 | `max_probe 0.03-0.05` | 按 YAML |
| 冷却 | `cooldown_days 3-5` | 按 YAML |

> **不新增仓位函数**；现有仓位函数读取 `caps`，按 `theme.id`/`track.id` 查表。总仓位/现金/下跌浪仓位仍由现有函数决定，本系统不重复实现。

### 7.2 轮动

- **主线**：月度重算；月内不因日频 top1 改变。
- **赛道**：周度确认；连续 2 周不在 top3 才降级。
- **细分**：周/日；新 `focus` 需连续 2 天 top3 + 硬规则；旧 `focus` 连续 2 天不在 top3 才退出。
- **个股**：日频；触发/止盈/止损按选股 agent。

### 7.3 卫星题材

- 资源/农业/军工/金融/医药/基建：只在 **题材仓**，≤10-15%，不参与主线评价。
- 明确不做就空着；不要为了“每天都满仓”去碰。

---

## 8. 评价层：怎么证明“准”

### 8.1 指标

| 层 | 指标 | 目标 |
|---|---|---|
| L0 主线 | 月度主线命中（AI/科技 vs 他的月度 top2） | 8/8（当前） |
| L1 赛道 | 月度赛道 top3 命中 | ≥75% |
| L2 细分 | 周/月细分 top3 命中 | ≥70% |
| L2 细分 | 日频 top1/top3 | top1 ≥40%、top3 ≥70% |
| L3 个股 | 判例一致率、误杀/误放、反证质量 | 20-30 个 case |
| 全链 | focus 方向后 5 日相对科技 ETF 超额 | 正超额≥55% |

**不问胜率**；用判例一致率、误杀/误放、反证质量、数据冲突发现数、稳定性。

### 8.2 判例集

- 2026-10-23：机器人 focus、存储 watch、PCB/材料 trend；
- 2026-10-21：设备 ETF→PCB/二线光/材料；
- 2026-09-02：材料/存储，避开大芯/大光；
- 2026-07-02：存储材料；
- 2026-05-09：存储映射（江波龙）；
- 2025-11-21：存储模组 vs 设备；
- 2025-05-29：机器人筹码干净二买；
- 2026-02/03/07：非科技题材 vs 主线。

### 8.3 验证方法

- **盲测**：新 subagent，不给日期/结论，只给 facts + rules。
- **留出**：3-5 个设计时没看过的日期/标的。
- **换年份/换板块**：2021-2022、2025、2026。
- **失败注入**：缺数据、gap 小、regime 翻转、P2/P3 拦截、重复/过期数据。
- **失败日志**：每次修正来自具体失败，不来自“想得更周全”。

---

## 9. 工程落地：文件、表、调度

### 9.1 文件

```
config/mainline/
  README.md                        # P0/P0.5
  seeds.yaml                       # P0.5 语义种子（不含代码）
  caps_policy.yaml                 # P0.5 caps 策略
  universe_mainline.yaml           # P0 参照
  universe_track.yaml              # P0 参照（P0.5 后生产读 generated/）
  universe_theme.yaml              # P0 参照（P0.5 后生产读 generated/）
  rules_mainline.yaml              # P0 已交付
  rules_track.yaml                 # P0 已交付
  rules_theme.yaml                 # P0 已交付
  generated/                       # P0.5 生成物（resolver 输出）
    universe_track.yaml
    universe_theme.yaml
    resolution_report.json
    diff_report.md
scripts/
  mainline_p0_audit.py             # P0 审计（只读）
  mainline_resolve_universe.py     # P0.5 resolver
jobs/
  mainline_monthly.py              # P1
  track_weekly.py                  # P2
  theme_daily.py                   # P3
  mainline_validate.py
  mainline_outcome.py
data/mainline/{as_of}/
  mainline.json
  track.json
  theme.json
  validation.json
docs/
  mainline-system-plan.md          # 本文
  mainline-failure-log.md
  mainline-universe-changelog.md
```

### 9.2 PG 表（append-only）

```sql
create table if not exists mainline_runs (
  id bigserial primary key,
  as_of date not null,
  freq text not null,              -- monthly / weekly / daily
  layer text not null,             -- L0 / L1 / L2
  payload jsonb not null,
  validation jsonb not null,
  created_at timestamptz default now(),
  unique (as_of, freq, layer)
);

create table if not exists mainline_outcomes (
  id bigserial primary key,
  run_id bigint references mainline_runs(id),
  layer text not null,
  name text not null,
  verdict text not null,
  t1_rel numeric, t3_rel numeric, t5_rel numeric,
  max_dd numeric,
  recorded_at timestamptz default now()
);
```

### 9.3 调度

| 任务 | 时间 | 说明 |
|---|---|---|
| L0 主线 | 每月第一个交易日 08:30 | 月频；月中只在 regime 突变时重算 |
| L1 赛道 | 每周一 08:30 | 周频确认；月度更新 |
| L2 细分 | 每日 15:10 | 盘后；盘中可选 14:40 `tail_preview` |
| L3 个股 | 15:20 | 只在 L2 focus/watch |
| 推送 | 15:25 | QQ 短版；无变化静默 |
| 结果 | T+1/3/5 | 分层跟踪 |

### 9.4 失败/幂等/静默

- 数据缺失 → 非零退出 + ❌，不输出过期报告。
- 唯一键 `as_of+freq+layer`；`on conflict do nothing`。
- 变更键 `as_of+layer+focus_names+verdict`；无变化静默。
- `--dry-run` 默认；`--apply` 才写库/推送。
- 每次运行跑真实链路，不只跑 dry-run 逻辑。

---

## 10. 分期实施

| 阶段 | 工期 | 产出 | 验收 |
|---|---|---|---|
| P0 分层/宇宙 | 1-2 天 | 三个 universe + 规则文件 | 每层有 leader/ladder/benchmark/API |
| P1 L0 主线 | 2-3 天 | `mainline_monthly.py` + 回测 | 2026 月度 8/8；2021-2025 回测 |
| P2 L1 赛道 | 3-5 天 | `track_weekly.py` + 申万 L2 | 月度赛道 top3 ≥75% |
| P3 L2 细分 | 3-5 天 | `theme_daily.py` + SW L3/CSI/东财 | 10 个判例；top3 ≥70% |
| P4 L3 个股 | 复用 | `stock-selector-agent-plan.md` | 20-30 个判例 |
| P5 影子/推送 | 2-4 周 | shadow → advisory | focus 命中不下降；误杀 ≤1 |

**先 shadow，后 advisory，永不自动下单。**

---

## 11. 风险与边界

1. **2026 是科技年**：主线 8/8 有 regime 成分；2021-2022 非科技年要换主线宇宙。
2. **中观数据滞后**：CSI 权重按月/季；东财成员当前快照，非 PIT；SW 成员有 `in_date/out_date`，最可靠。
3. **概念重叠**：CPO/光模块、PCB/元件、存储/半导体高度重叠；先赛道后细分，不 flat 比较。
4. **日频噪声**：日频 top1 只有 42.6%；只能 top3 + 规则，不能单押。
5. **Agent 自证**：facts 程序算、agent 不取数/不写库、stateless、程序校验。
6. **数据源单点**：datahubco/promax/Tushare 都可能抖动；失败要响，不硬凑。
7. **规则过期**：语料同步到 2026-10-16；新说法要人工入规则 + 误杀测试。
8. **不要为了回测好看加规则**：每条新规则先套已知判例，误杀就说明测的不是专家的东西。

---

## 12. 一句话

**主线系统 = 月频 AI/科技（8/8）→ 月/周申万赛道 → 周/日细分（SW L3 + 中证主题 + 东财概念）→ 日频个股；主线不按日频改，细分不单押 top1，卫星题材单独记账，程序给事实、agent 给判断、自动闸给否决、人只做审计。**

---

## 13. P0 交付（2026-10-26）

### 13.1 已交付

| 文件 | 内容 |
|---|---|
| `config/mainline/README.md` | 层级、分级、caps 语义、数据源、审计用法 |
| `config/mainline/universe_mainline.yaml` | L0：AI/科技（active）+ 新能源/医药（backup） |
| `config/mainline/universe_track.yaml` | L1：11 条科技赛道 + 4 条卫星赛道，含 sources/leader/ladder/caps |
| `config/mainline/universe_theme.yaml` | L2：23 个细分 + 4 个卫星题材，含 CSI/SW/东财 sources |
| `config/mainline/rules_mainline.yaml` | L0 规则（月频、确认/不确定、卫星不计入主线） |
| `config/mainline/rules_track.yaml` | L1 规则（top3、相对强度、宽度、大哥/梯队、拥挤、迟滞、caps） |
| `config/mainline/rules_theme.yaml` | L2 规则（父赛道、量价、业绩、CPO/PCB/存储特殊规则、闸门） |
| `scripts/mainline_p0_audit.py` | 只读审计：结构/代码存在/成员/历史/leader/caps |

### 13.2 审计结果

- `--fast`：**0 errors / 0 warnings / 42 items**。
- 全量：**0 errors**；warning 主要是：
  - 部分东财 `dc_member` 因 token/上游 40203 失败（板块级/宽度仍可用 `dc_index`）；
  - `storage` 的 CSI 932580 历史仅 55 行（新指数，回测用东财/THS 补）；
  - 少数 SW L3 代码存在 SW2014/SW2021 版本差异（`sw_daily` 0 行，成员用 `index_member_all`）。

### 13.3 明确未做（留给 P1/P2/P3）

- 没有新增仓位函数；caps 只定义，等现有仓位函数读取。
- 没有 `mainline_monthly.py` / `track_weekly.py` / `theme_daily.py`。
- 没有 agent、没有推送、没有回测、没有 PG 表。
- 没有写总仓位/现金/下跌浪仓位规则（由现有仓位函数负责）。

### 13.4 P0 验收标准

- 所有 L0/L1/L2 条目有 `id/name/caps/sources`；
- 所有代码能在 Tushare 查到（CSI/SW/东财）；
- 所有 leader/ladder 在 `stock_basic`；
- 所有 theme 的 `parent_track` 存在，所有 track 的 `theme` 引用存在；
- `mainline_p0_audit.py --fast` 0 errors；
- 不出现 `total_position` / `cash` / `down_wave_position` 字段。


---

## 14. P0.5 交付（2026-10-27）

### 14.1 做了什么

- 新增 `seeds.yaml`：只写主题名/别名/层级意图/是否卫星/父赛道提示，**不写代码**。
- 新增 `caps_policy.yaml`：caps 由策略 + 成员数推导，不逐项常数。
- 新增 `scripts/mainline_resolve_universe.py`：
  * 按名称/别名匹配 CSI、申万 L3、东财、同花顺候选；
  * 按成员数/历史/父赛道重叠/纯度打分；
  * 父赛道用“主题成员 ∩ 申万 L2 成员”覆盖率推导，输出 `parents` 多父图；
  * caps 由策略推导，并 clamp 到父 track 上限；
  * 输出 `generated/universe_*.yaml`、`resolution_report.json`、`diff_report.md`。
- 手写 `universe_*.yaml` 降级为参照/回归；生产读 `generated/`。

### 14.2 结果

- 15 条 track（11 科技 + 4 卫星）、27 个 theme（23 科技 + 4 卫星）。
- `mainline_p0_audit.py --fast --dir config/mainline/generated`：**0 errors / 0 warnings / 42 items**。
- diff 主要变化：
  * 部分 source 从“手写 CSI/东财”换成 resolver 认为更纯的 SW L3/CSI/东财；
  * 部分 track 的 SW L2 从 SW2014 代码换成 SW2021 可用的代码（如 automation 801078、computer_application 801103）；
  * caps 由成员数推导（如半导体 track 0.35→0.25、medicine 0.15→0.25），并 clamp 到父 track。

### 14.3 已知限制

- **leader/ladder 仍用 P0 参照**（`leader_method: reference_pending_p1`）。原因：概念成员噪声会让自动推导误选“寒武纪/中国电信”这类非核心；推导候选记录在 `resolution.derived_leader`。
- 部分 source 在语义上等价但不完全一致（如 PCB 用东财 BK1340 而不是 CSI 932666；电池用 BK1033 而不是 BK1303）；diff 里逐条列出，P1 再定“主/备”角色。
- SW2014/SW2021 代码差异仍在；audit 对 `sw_daily` 0 行只 warning，回测用成分股聚合。

### 14.4 P1 接管

1. **core score**：leader/ladder 从 `reference` 切到“权重 + 20 日 RS + 市值 + 成交额 + 业绩”的 composite，先在 10/23、10/21、09/02、07/02、05/09 五个判例上验证。
2. **source 角色**：primary（纯度/成员）、secondary（宽度）、crosscheck（THS）；diff 里人工确认一次。
3. **caps 校准**：用 2026 回测/判例校准 `caps_policy` 的 base/range，不逐项手改。


---

## 15. P0.6 agent 复核试验（2026-10-27）

### 15.1 做了什么

- 用一个 subagent 对 5 个歧义主题（storage / pcb / cpo / humanoid_robot / battery）做复核。
- 输入是 resolver 的候选事实 + 狼大规则 + 判例；输出是 `selected_sources / parent_track / leader / ladder / confidence / needs_human`。
- 新增 `scripts/mainline_agent_review.py`：校验 agent 输出（source 在候选、parent 存在、leader/ladder 在允许集合），默认 shadow，`--apply` 只写 `generated/agent_applied_theme.yaml`。

### 15.2 结果

| 主题 | agent 选源 | agent 核心 | confidence | needs_human |
|---|---|---|---|---|
| storage | 932580.CSI + BK1137.DC | 兆易创新 | 0.70 | false |
| pcb | 850822.SI + BK1340.DC | 胜宏科技 | 0.84 | false |
| cpo | 931723.CSI + BK1136.DC | 中际旭创 | 0.78 | false |
| humanoid_robot | H30590.CSI + 850781.SI | 宇树科技 | 0.80 | false |
| battery | 931555.CSI + 931664.CSI | 宁德时代 | 0.55 | **true** |

校验：**0 errors / 1 warning**（battery needs_human）。

### 15.3 agent 相对 resolver 的增量

- **修复核心**：storage 兆易（resolver 推澜起）、PCB 胜宏（resolver 推沪电）、humanoid 宇树（resolver 推汇川）、battery 宁德（resolver 推汇川）。
- **去噪**：CPO 弃 BK1128（history=0/混入非模块）、battery 弃 BK1033（108 成员/无区分度）、PCB 弃 BK0877（205 成员/噪音桶）、humanoid 弃 BK1184（312 成员/杂票多）。
- **校准**：battery 父赛道重叠 0、源之间 score 接近，agent 不硬猜，标 `needs_human=true`。

### 15.4 结论与边界

- **agent 适合**：候选语义、核心/大哥、diff 复核。
- **agent 不适合**：取数、代码匹配、成员计算、caps 数值。
- 当前仍是 **shadow**：agent 结果不自动覆盖生产；`agent_applied_theme.yaml` 只作对照。
- 下一步：把复核扩到全部歧义主题；validator 增加“leader/ladder 必须在选中 source 成员里”；人工确认 battery 后再 `--apply`。


---

## 16. P0.6b 复核扩到 15 个主题（2026-10-27）

### 16.1 范围

第一轮 5 个（storage / pcb / cpo / humanoid_robot / battery）+ 第二轮 10 个：
semi_equipment、semi_material、chip_design、packaging、compute、ai_app、liquid_cooling、robot_actuator、satellite、space。

### 16.2 结果

- 15 themes：**0 errors / 7 warnings**。
- `needs_human=true`：battery、packaging、compute、liquid_cooling、space。
- ladder 不足 3 个：liquid_cooling、space（各 2 个）。

| 主题 | agent 选源 | agent 核心 | 置信度 | needs_human |
|---|---|---|---|---|
| semi_equipment | 850818.SI + BK1326.DC | 北方华创 | 0.88 | false |
| semi_material | 850813.SI + BK1325.DC | 鼎龙股份 | 0.82 | false |
| chip_design | 850814.SI + 932040.CSI | 兆易创新 | 0.76 | false |
| packaging | 850817.SI + BK1101.DC | 长电科技 | 0.66 | **true** |
| compute | 931688.CSI | 浪潮信息 | 0.60 | **true** |
| ai_app | BK1629.DC + 931071.CSI | 金山办公 | 0.71 | false |
| liquid_cooling | BK1138.DC | 英维克 | 0.58 | **true** |
| robot_actuator | BK1145.DC + BK1100.DC | 三花智控 | 0.83 | false |
| satellite | 931585.CSI + 931594.CSI | 中国卫星 | 0.72 | false |
| space | 857411.SI | 航发动力 | 0.30 | **true** |

### 16.3 agent 的修正

- **semi_material**：弃 931743（材料+设备混合），用 850813 + BK1325，核心鼎龙；derived 先导基电/设备链被剔除。
- **chip_design**：弃 932087 集成电路（含制造/封测），用 850814 + 932040，核心兆易；derived 寒武纪被剔除。
- **packaging**：弃 derived 寒武纪/北方华创/京东方，核心长电；但父赛道重叠 0.372、ladder 3 个，标 needs_human。
- **compute**：弃 931656（overlap 0.148）与 BK1134（249 成员），只用 931688，核心浪潮；父赛道 hint 重叠 0，标 needs_human。
- **liquid_cooling**：弃 derived 中兴/亨通/光迅，核心英维克；只有单源、overlap 0.067、ladder 2 个，标 needs_human。
- **robot_actuator**：保留三花，弃 derived 绿的/鼎智/埃夫特。
- **satellite**：保留中国卫星，弃 derived 移远通信。
- **space**：名称池全是航空标的，agent 不编造航天股票，标 needs_human，等人工补纯航天 leader/ladder。

### 16.4 结论

- agent 在 15 个主题上 **0 代码错误**，且能识别 5 个数据冲突/口径不足的主题，不硬猜。
- 当前仍是 shadow；`agent_review_expanded_applied_theme.yaml` 只作对照。
- 下一步：人工处理 5 个 needs_human；validator 增加“leader/ladder 必须在选中 source 成员里”；通过后再决定是否替换生产。


### 16.5 用户确认与 promote（2026-10-27）

- 用户确认：`needs_human` 直接确认，不再人工 gate；后续 `needs_human` 同样自动确认。
- 新增 `config/mainline/review_policy.yaml`：`auto_confirm_needs_human: true`，保留 `data_conflict` 审计位。
- 5 个 needs_human 全部确认：
  * battery：宁德 + 比亚迪/亿纬/赣锋/国轩；父赛道 overlap 0，保留 `data_conflict`。
  * packaging：长电 + 通富/华天/甬矽；父赛道 0.372、ladder 3，保留 `data_conflict`。
  * compute：浪潮 + 曙光/紫光/中兴/润泽；父赛道 hint overlap 0，保留 `data_conflict`。
  * liquid_cooling：英维克 + 申菱/欧陆通；单源、overlap 0.067、ladder 2，保留 `data_conflict` 与 ladder 警告。
  * space：原名称池全是航空，按 `857411.SI` 成员补纯航天核心（中国卫星/中国卫通/航天电子/航天环宇），不是把航发动力当航天核心。
- 已 promote：`generated/universe_theme.yaml`（备份 `universe_theme.before_agent.yaml`）。
- 审计：`mainline_p0_audit.py --fast --dir generated` = **0 errors / 0 warnings / 42 items**。
- 后续 12 个主题继续复核时，`needs_human` 自动确认，只保留 `data_conflict`。


---

## 17. P0.6c 全量 27 主题复核（2026-10-27）

### 17.1 范围

15 个已复核 + 剩余 12 个：
passive、optical_module、communication_transmission、pv、optical_component、aviation、innovative_drug、medical_device、resources、agriculture、finance、construction。

### 17.2 结果

- **0 errors / 9 warnings**（8 个 data_conflict + agriculture 保留现有 source）。
- 全部 `needs_human` 按用户策略自动确认。
- 已 promote 到 `generated/universe_theme.yaml`，27 个主题全部 `agent_reviewed=true`。
- 审计：`mainline_p0_audit.py --fast --dir generated` = **0 errors / 0 warnings / 42 items**。

### 17.3 关键修正

| 主题 | 源 | 核心 | 备注 |
|---|---|---|---|
| passive | 850823.SI + BK1339.DC | 顺络电子 | 三环/法拉/风华/铜峰梯队 |
| optical_module | 931723.CSI + BK1136.DC | 中际旭创 | 与 CPO 共用光通信源，模块细分 |
| communication_transmission | 851024.SI + BK1592.DC | 亨通光电 | 明确不把中际旭创当传输核心 |
| pv | 931151.CSI + 857354.SI | 隆基绿能 | data_conflict：父赛道 overlap 0 |
| optical_component | 850833.SI + BK1334.DC | 水晶光电 | 不把京东方（面板）当光学元件核心 |
| aviation | 857421.SI + BK1384.DC | 中航沈飞 | 航发/西飞/成飞/洪都/西部超导 |
| innovative_drug | 931152.CSI + 950161.CSI | 恒瑞医药 | 不把化学制剂宽口径当创新药 |
| medical_device | H30217.CSI + 851532.SI | 迈瑞医疗 | 不把 340 成员概念当纯器械 |
| resources | 932256.CSI + 850531.SI | 紫金矿业 | 铜金双线，卫星短差 |
| agriculture | 保留 801017.SI | 牧原股份 | 候选源仅 3/3/0 成员，无合格源 |
| finance | 851931.SI + 932133.CSI | 中信证券 | 券商+银行双线 |
| construction | 931931.CSI + 932117.CSI | 中国建筑 | 只取施工央企三只 |

### 17.4 收尾

- `agent_review_all.json`：27 主题合并结果。
- 备份：`universe_theme.before_round3.yaml`（15 主题版）、`universe_theme.before_agent.yaml`（agent 前）。
- 后续：P1 把 27 主题接入 `track_weekly.py` / `theme_daily.py`，用 agent 结果作为初始 focus/watch/avoid，再按日频评分更新。


---

## 18. P1 实施（2026-10-27）

### 18.1 已交付

| 脚本 | 作用 |
|---|---|
| `jobs/mainline_common.py` | as-of、SW/CSI/东财指数、daily 快照、宽度、z-score |
| `jobs/mainline_monthly.py` | L0 月度主线：代理指数 20d 超额 → 子主线/regime/confirmed |
| `jobs/track_weekly.py` | L1 赛道周频：1d/5d/20d、宽度、leader RS、成交额比 → score/focus/trial/watch/avoid |
| `docs/p1-agent-decision.md` | P1 是否用 agent 的决策 |

### 18.2 2026-10-23 首跑

- `mainline.json`：AI/科技，子主线 AI/算力（-2.92%），半导体/芯片（-15.21%），`regime=pullback`，`confirmed=false`。
- `track.json`：
  * focus = 空（主线未确认，focus 自动降为 trial）；
  * trial = 电气自动化设备、计算机应用、计算机设备；
  * watch = 半导体、航天装备；
  * avoid = 光学光电子、航空装备、医药、通信设备、电源设备、元件；
  * satellite_focus = 农业。
- 说明：track 级别是周频，窗口含 9/25-10/23 科技回撤；10/23 单日的机器人/存储/PCB 强要等 P2 theme 日频，P1 不能直接当买卖信号。

### 18.3 P1 的 agent 决策

- **主评分不用 agent**：分数是可复现算术，agent 不取数、不写分数。
- agent 只在两处复核：
  1. 月度主线切换（AI/算力 vs 半导体 vs 非科技），每月一次；
  2. 周频 track top3 歧义（分差 <0.5 / data_conflict / 与 theme 复核不一致），每周一次。
- 默认 shadow；只能改 `verdict`，不能改分数/代码；不确定保留机械结果。

### 18.4 下一步

- P2：`theme_daily.py`，用 27 主题的 source/leader/ladder 做日频 focus/watch/avoid；
- P2 判例：10/23、10/21、09/02、07/02、05/09；
- 通过后再接现有仓位函数的项目级 caps。


---

## 19. P2 L2 细分日频（2026-10-27）

### 19.1 交付

| 脚本 | 作用 |
|---|---|
| `jobs/theme_daily.py` | 27 主题日频：源收益、宽度、leader RS、成交额比 → score/focus/trial/watch/avoid |

输出：`data/mainline/{as_of}/theme.json`。

### 19.2 评分与 gate

```text
score = 0.20*z(ex1) + 0.35*z(ex5) + 0.20*z(breadth5)
      + 0.15*z(leader_rs5) + 0.10*z(amount_ratio)
```

- 主线 `confirmed=false`：focus → trial；
- 父赛道 `avoid`：focus → watch；
- 卫星题材单独 focus/watch/avoid；
- 不做 crowding 硬删，P2.5 再加。

### 19.3 判例结果

| 日期 | trial | 关键 avoid |
|---|---|---|
| 2026-10-23 | 人形机器人、机器人执行器、AI 应用 | 存储、PCB、CPO、光模块、半导体设备 |
| 2026-10-21 | 人形机器人、卫星、机器人执行器 | 半导体设备、材料、先进封装、PCB |

- 10/23 人形机器人：ex1 +7.5%、ex5 +15.3%、breadth 90%、leader 宇树 +31.5%，机械分数第一。
- 10/23 存储：ex1 -1.0%、ex5 +0.5%、breadth 59%，机械 score -0.16，排 14/23 → avoid；与“存储只做短差/watch”的叙事有差距，说明 P2 机械分数偏严，需要 P2.5 用 agent/规则复核。
- 10/21 设备/PCB/材料：机械分数仍 avoid，未确认“设备 ETF→PCB/二线光/材料”的叙事；这正是 P2 要看 facts 而不是先验结论。

### 19.4 agent 在 P2 的位置

- **主评分不用 agent**：日频分数是机械的，agent 不取数、不改分数。
- 27 主题的 agent 复核是**静态宇宙输入**（选源/核心/梯队），不是日频信号。
- 可选 P2.5：对 theme top3 分差 <0.5 / 与 P1 track 冲突 / data_conflict 的主题做 agent 复核，只改 verdict，默认 shadow。

### 19.5 下一步

1. 用 5 个判例（10/23、10/21、09/02、07/02、05/09）校准权重/window；
2. 加 crowding（公募拥挤）软扣分；
3. 接 P1 track 的 parent gate 和项目级 caps；
4. 通过后再谈 P2.5 agent 复核。


---

## 20. P2.5 拥挤度 + caps + agent 复核（2026-10-27）

### 20.1 已加

- `theme_daily.py`：
  * `crowding_avg / crowding_max / crowding_penalty`，用 `data/fund_holdings/aggregate.json`，只取 `latest_end_date <= as_of`；
  * 每行输出 `caps`，供现有仓位函数读取；不新增总仓位函数。
- `scripts/theme_agent_review.py`：
  * 校验 agent 对 theme verdict 的复核（theme_id 存在、verdict 合法、mainline 未确认禁止 focus、confidence 0-1）；
  * `--apply` 写 `theme_agent_applied.json`，`--promote` 才更新 `theme.json`（备份）。

### 20.2 2026-10-23 结果

拥挤惩罚：
- 人形机器人 crowd_avg 1.58 / max 4.64 → +0.146；
- 存储 crowd_avg 1.31 / max 6.19 → +0.079；
- PCB crowd_avg 0.273 / max 0.77 → -0.181。

agent 复核（7 主题，0 errors）：
- trial：humanoid_robot、robot_actuator；
- watch：medical_device、optical_component、ai_app；
- avoid：storage、pcb；
- 机械与 agent 完全一致，没有越权升级。

### 20.3 待做

- 09/02、07/02、05/09 三个判例的权重/window 校准；
- 拥挤惩罚阈值（当前 0.15*z）在 5 个判例上复核；
- 接 P1 track 的 parent gate + 现有仓位函数的项目级 caps；
- 之后再谈 P2.5 的自动应用（当前 shadow）。


### 20.4 校准发现（2026-10-27）

- 修复：
  * 非交易日 as-of 回退到上一交易日（05/09 → 05/08）；
  * `trade_dates` 窗口改为 as-of 前 150 天，避免 07/02、05/08 的 5d/20d 失真。
- 同日 top3 命中：10/23 2/5、10/21 0/4、09/02 0/2、07/02 0/2、05/08 0/1。
  * 但判例是“未来方向”，同日排名不是正确指标。
- 前向 T+5：
  * 05/08 存储 +0.0%，机械 top 航天 -9.8%、机器人执行器 -1.1%；
  * 07/02 存储 +1.4%、半导体材料 -8.5%，机械 top 创新药 -1.7%、航天 -2.2%；
  * 09/02 半导体材料 -1.2%、存储 -0.2%，机械 top 航空 +2.0%、液冷 +0.4%、AI 应用 +0.9%。
- 结论：5 个 case 不足调权重；机械 top3 与判例主题互有胜负，**不过拟合**。下一步 20-30 个前向 case，再调权重/window。
- agent 10/23 复核 7 主题：全部维持，0 errors；仍 shadow，不 promote。


### 20.5 P2.5b 前向权重网格（21 cases）

- 新增 `scripts/theme_forward_eval.py`：2026 判例日 → 27 主题，主 source 1d/5d/20d/amount 网格。
- 当前权重 hit3 0.38 / forward +1.53%；最佳 hit3 0.67（w1=0,w5=0.2,w20=0.2,wa=0.1）/ forward +0.81%；最佳 forward +1.69%（w1=0,w5=0.4,w20=0.1,wa=0）。
- 结论：加 20d、去掉 1d 明显提高 top3 命中；不做单点最优过拟合。
- 已落 `config/mainline/theme_weights.yaml`：ex1 0.00、ex5 0.30、ex20 0.20、breadth 0.25、leader 0.15、amount 0.10。
- 10/23、10/21 重跑：trial 仍机器人组；10/21 航天升 trial；存储/PCB 仍 avoid。
- 日期修复：非交易日回退上一交易日；trade_dates 改为 as-of 前 150 天；05/09→05/08。


### 20.6 P2.5c 95-case holdout 与双 profile

- 样本 95 个判例日：train 76（~2026-01~06），holdout 19（2026-06-30~08-14）。
- default（0.2/0.4/0/0.1）：train hit3 0.43 / T+5 +2.51%；holdout 0.26 / -2.03%。
- accuracy（0/0.3/0.2/0.1）：train 0.51 / +2.98%；holdout 0.53 / -3.15%。
- 其他 20d 变体：train 0.53-0.55 / +2.8~3.0%；holdout 0.53 / ~-3.0%。
- 结论：加 20d 提高 top3 命中，但 holdout T+5 变差；没有单一大满贯权重。
- 处理：生产默认 `theme_weights.yaml`（default，偏 T+5）；`theme_weights_accuracy.yaml` 作为研究/切换 profile；不自动 promote。
- `track.json` 已加 `caps` / `project_caps`；theme caps 已在 `theme.json`。
- 下一步：按 regime（up/pullback）分段，决定是否动态切换 profile。


### 20.7 P2.5d 判据匹配（修正）

- 用户修正：P2.5 不是收益率优化，持仓 5 天，先把判据匹配做对。
- `theme_daily.py` 增加：
  * `holding_days: 5`；
  * `criteria`：mainline/parent/leader/breadth/amount/trend/crowding；
  * `criteria_pass`；focus/trial 要求 >=4；卫星 focus 同样受限。
- 校验：`scripts/theme_criteria_check.py` 10/23、10/21 = 0 errors。
- 结果：
  * 10/23 trial：人形机器人、机器人执行器；satellite 空；
  * 10/21 trial：人形机器人、机器人执行器、航天；satellite 空。
- `theme_forward_eval.py` 仅诊断，不调生产权重；生产 default，accuracy 仅研究。
- 5 天持有：用 criteria_pass + 父赛道/主线 gate 判断继续持有，不按 T+5 收益追涨杀跌。


### 20.8 P2.5e 5 天持有：只输出判据有效性

- `holding_policy.yaml`：holding_days 5、criteria_pass_min 4、review_on_fail true；不再定义 reduce/exit。
- `theme_daily.py` 输出 `thesis_valid` / `review_required` / `failed_criteria`。
  * thesis_valid = criteria_pass>=4 且 parent_ok 且 trend_ok；
  * review_required = not thesis_valid 或 mainline weak；
  * hold/reduce/exit 交给现有仓位函数。
- 5 case：10/23 trial 人形机器人/机器人执行器；10/21 trial 人形机器人/机器人执行器/航天；09/02 trial 航空装备/液冷/AI 应用；07/02 trial 创新药/航天；05/08 focus 航天/机器人执行器。
- `theme_holding_check.py` 0 errors；`theme_criteria_check.py` 5 case 0 errors。
- 数据缺口：09/02、07/02 的机器人执行器/液冷历史 `dc_daily` 无数据；P2.6 加 SW/CSI 代理兜底。


### 20.9 09/02、07/02 缺失原因与修复

- 直接查 `dc_daily`：BK1145/BK1100/BK1138 均有 193 行，20260702、20260902 都有数据；不是历史缺失。
- 原因：多日期循环中 `dc_daily` 偶发限流/400/502；`theme_daily` 对单个 source 失败直接跳过。机器人执行器两个 source 都是东财，两个都失败即无数据；液冷只有单源，失败即无数据。
- 复现：单独重跑 09/02、07/02 不再报错。
- 修复：`fetch_series` 重试 3 次 + 缓存；失败写 `source_errors`；P2.6 加 SW/CSI 代理兜底。


### 20.10 P2.6 接到现有仓位函数读取层

- 新增 `backend/app/services/mainline_read.py`：读 `data/mainline/{as_of}/theme.json`/`track.json`/`mainline.json`，按 `theme_id`/`track_id` 或 symbol 的 leader/ladder 查找，返回 `thesis_valid`/`review_required`/`failed_criteria`/`caps`/`holding_days`。
- `CalcPositionRequest` 新增可选 `theme_id`/`track_id`；`CalcPositionResponse` 新增 `mainline` 上下文。
- `calc_position` 只做 caps 收紧：
  * max_single_stock → 单票上限；
  * max_probe → 试探仓上限；
  * max_add → 加仓上限；
  * allow_new/allow_add/allow_t=false → 禁止对应 intent；
  * thesis_valid=false / review_required=true → warning，不直接改仓位。
- 不新增总仓位函数，不改总仓/现金。
- 测试：`backend/tests/test_mainline_read.py` 3 passed。
- 已知：同一股票可属多个主题；symbol 查找优先非 avoid，精确场景传 theme_id。


### 20.11 P2.7 调用方带 theme_id + 前端展示

- `CalcPositionRequest` 支持 `theme_id`/`track_id`。
- 自动调用方在 `calc_position` 前解析 context：`candidate_pool_monitor`、`long_term_pool_monitor`。
- 工具/前端：`toolRegistry.ts` 的 `calc_position` 增加 `theme_id`/`track_id`；`docker/dsh/bridge/lib/index.js` 透传。
- `ChatContainer.tsx` 为 `calc_position` 注册专用 renderer，在 JSON 上方显示 `📌 主题 · verdict · thesis_valid · review_required`。
- 验证：frontend build ✅；`test_mainline_read.py` 3 passed。


---

## 21. P3 个股层（第一版，2026-10-27）

### 21.1 个股候选

- `jobs/stock_select.py`：从 `theme.json` 的 focus/trial 主题出发，取主题 YAML 的 leader/ladder，输出 5 日涨幅、收盘、市值、主题 verdict、thesis_valid、caps。
- 只列候选，不替用户下单。
- 10/23 候选：宇树 +46.1%、兆威 +50.1%、绿的 +22.5%、拓普 +12.5%、三花 +11.7%、鸣志 +35.4%、五洲 +17.3%、雷赛 +11.2%。

### 21.2 5 天持仓复核

- `jobs/holding_review.py --symbol ... --days 5`：只列每天涨幅/收盘/累计，不输出 hold/reduce/exit。
- 上下文带 theme/verdict/thesis_valid/review_required/caps；买卖由用户判断。
- 持有期从 20 天改为 **5 天**；`holding_policy.yaml` version 2。

### 21.3 下一步

- 选股 agent 接入：从候选里做 `probe/watch/reject`，再走 P2/P3 gate + calc_position；
- 持仓复核每天列涨幅，不自动做动作；
- 组合层/收益回测后置。


### 21.4 P3 选股逻辑审计

- 用户指出选股逻辑不对；审计见 `docs/p3-stock-selection-audit.md`。
- 旧逻辑问题：leader/ladder 当核心/龙头、5 日涨幅排序、主题 verdict 当个股许可、max 8 自设上限、ST 过滤只覆盖低位规则。
- 已改 `jobs/stock_select.py` 为 facts-only：不排序、只输出事实（1d/5d/20d、20 日高低、成交额/换手/市值排名/拥挤/主题 caps）。
- 下一步按 B1 原话做分类层（核心/风向标/龙头/龙2/杂毛），case-based 验证，不做单因子回测定阈值。


### 21.5 P3 agent 选股试验（2026-10-23）

- 候选池改为主题 source 成员（人形 72 / 执行器 106），不再只用 leader/ladder。
- agent 选股：
  * 人形：三花（核心/probe）、宇树（风向标/watch）、大族（杂毛/reject）；needs_human=true（营收拐点缺失）。
  * 执行器：拓普（二供/probe）、绿的（龙头/watch）、山子（杂毛/reject）；needs_human=false。
- 校验 `scripts/stock_agent_review.py` = 0 errors；只允许候选内代码。
- 文件：`stock_agent_facts.json`、`stock_agent_review.json`、`stock_agent_validated.json`、`stock_agent_selected.json`。
- 下一步：扩到其他 trial 主题/日期；probe 候选再走 P2/P3 gate + calc_position（带 theme_id）。


### 21.6 10/28 盘中数据保护与 10/27 完整流程

- 2026-10-28 09:54：`daily(20261028)` 0 行，盘中未收盘；`track_weekly`/`theme_daily`/`stock_select` 加 guard，exit 2，不输出 0% 宽度假信号。
- 10/28 15:00 后重跑完整命令；agent 选股基于 `stock_agent_facts.json`。
- 10/27 完整流程：
  * mainline AI/科技，AI/算力 -19.24%，半导体 -21.03%，regime weak，confirmed false；
  * track trial 电气自动化/计算机应用/光学光电子；
  * theme trial 人形机器人/机器人执行器；
  * stock facts 人形 72/执行器 106，候选 16/17；stock_candidates 8 只事实。


### 21.7 P3 agent 选股 10/27（补跑）

- 10/27 首轮只到 facts，未调 agent；现已补调并校验。
- 人形：绿的谐波（二供/probe）、汇川（核心/watch）、石头（杂毛/reject）。
- 执行器：三花（核心/probe）、长盈（风向标/watch）、山子（杂毛/reject）。
- 差异：10/27 regime=weak，宇树换手 23% 过热不再 probe；改为回调充分的绿的二供；长盈 +48% 只作风向标。
- 校验 0 errors；文件 `data/mainline/20261027/stock_agent_{review,validated,selected}.json`。


### 21.8 主线 confirmed 诊断（2026-10-27）

- 23 采样日 confirmed 34.8%；月度 06 60%、07 0%、08 20%、09 80%、10 0%。
- 10/27 AI/算力 20d -26.87%、半导体 -28.67%、上证 -7.64%，绝对/相对都为负 → `mainline_exists=true, tradeable=false, status=weak`。
- 规则偏严：confirmed 要求 20d 超额>0（基准上证）；AI 绝对上涨 43.5% 的样本里只有 47.8% 超额为正，绝对涨但跑输会被判 false，应区分 `pullback`。
- 数据缺口：10/27 931656/931657 缺失，AI 覆盖 0.5；08/18 932580 仅 16 行，覆盖 0.75；旧逻辑 coverage<0.8 直接 false，需与真实弱势分开。
- 已加 `status/abs_ok/rel_ok/cov_ok/data_warning`；下一步 coverage 用可用代理兜底，并考虑 tech-internal benchmark/rank。


### 21.9 主线语义修正：mainline_exists / tradeable

- 用户拍板：`no_mainline` 改为 `mainline_exists=true, tradeable=false`。
- `mainline_monthly.py` 输出：
  * `mainline_exists`：配置的主线是否存在（有数据即 true）；
  * `tradeable`：只有 confirmed 才 true；
  * `status`：confirmed / pullback / weak（去掉 no_mainline）。
- `track_weekly.py` / `theme_daily.py` 同步输出 `mainline_exists`、`mainline_tradeable`、`mainline_status`；gate 用 `tradeable`。
- 10/27：`exists=true, tradeable=false, status=weak`；track/theme 均为 trial，不新开 focus。


### 21.10 mainline_active 字段

- 用户确认：增加 `mainline_active`，区分“配置主线”与“市场活跃主线”。
- `mainline_monthly.py`：
  * `mainline_exists=true`：AI/科技仍是配置主线；
  * `mainline_active = abs_ok and rel_ok`：市场当前是否认这条线；
  * `tradeable = confirmed`：能否执行。
- `track.json`/`theme.json` 同步 `mainline_active`；`mainline_read` 上下文也带。
- 10/27：`exists=true / active=false / tradeable=false / status=weak`。
- 基准 `confirmed` 不变；agent 工具复核与 `tradeable=false` 一致，仅 `mainline_exists` 语义差异由 `mainline_active` 解决。


### 21.11 P3 事实字段补齐

- `stock_agent_facts.py` 新增：
  * 60/120 日位置：`ret60/ret120`、`dist_high60/low60`、`dist_high120/low120`、`range_pos_120`；
  * ST/上市状态：`is_st`、`list_status`；
  * 业绩快报：`earnings{...}`，`status=ok/loss/missing`；
  * 老龙头组件：`leader{seed_role, limit_up_120d, top3_days_120_pool, pool_size}`。
- `top3_days_120_pool` 仅候选池口径，不是全主题；全主题需补成员历史。
- 10/27 已重跑：人形 16 / 执行器 17 候选。


### 21.12 高位/低位规则包（P3）

- `position_rules.yaml`：low: dist_high120<=-30%、range_pos_120<=0.35、ret60<=0；high: dist_high120>=-10%、range_pos_120>=0.65、ret60>=0 或 range_pos_120>=0.80；mid 其余。
- `position_rules.py` 输出 state / entry_mode / hold_bias / evidence / risk_flags / leader_evidence。
- `stock_agent_facts.py` 每只候选带 `position`；10/27 多数 low/ambush_candidate，科大讯飞 mid/neutral，无 high。
- 阈值标注“待判例校准”；`top3_days_120_pool` 为候选池口径，不是全主题。


### 21.13 低位埋伏加浪型/主线 gate

- `position_rules.py` 增加 `wave_gate`：读 `wave_state.operation` + `mainline_active`，低吸需 P3 允许 new_base/probe。
- 10/27 wave=defense、mainline_active=false → all low `watch`；high `no_chase`，defense 下 `hold_bias=卖强减仓`。
- 下一步：forward K 线测试只保留“浪型允许 + mainline active”的日期，再比较 low+leader vs 全种子。
