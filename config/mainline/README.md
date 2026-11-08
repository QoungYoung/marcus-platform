# config/mainline — P0 分层宇宙

> P0 只定义“层、宇宙、数据源、项目级 caps、规则”，不写仓位函数、不下单、不跑 agent。
> 总仓位/现金由现有仓位函数决定；本目录只提供**项目级上限**，供现有函数读取。

## 文件

| 文件 | 层 | 内容 |
|---|---|---|
| `universe_mainline.yaml` | L0 | 主线宇宙：AI/科技（active）、新能源/医药（backup） |
| `universe_track.yaml` | L1 | 赛道宇宙：申万 L2（半导体/元件/通信设备/…）+ 卫星赛道 |
| `universe_theme.yaml` | L2 | 细分宇宙：申万 L3 + 中证主题 + 东财概念 + 卫星题材 |
| `rules_mainline.yaml` | L0 | 主线规则：月频、确认/不确定、卫星不计入主线 |
| `rules_track.yaml` | L1 | 赛道规则：月频 top3、相对强度、宽度、大哥/梯队、拥挤、迟滞 |
| `rules_theme.yaml` | L2 | 细分规则：父赛道、量价、业绩、CPO/PCB/存储特殊规则、闸门 |
| `seeds.yaml` | — | P0.5 语义种子：主题名/别名/层级/是否卫星/父赛道提示，**不含代码** |
| `caps_policy.yaml` | — | 项目级 caps 策略（base + 成员数调整） |
| `README.md` | — | 本文件 |
| `generated/` | — | P0.5 生成物：resolver 从 seeds + Tushare 推导的 track/theme + diff/report |
| `scripts/mainline_resolve_universe.py` | — | P0.5 resolver（只读 Tushare，写 generated） |

## 层级

```
L0 主线（月/季）  AI/科技
L1 赛道（月/周）  半导体 / 元件 / 通信设备 / 计算机设备 / 自动化 / 电源设备 / 航天 / 医药
L2 细分（周/日）  存储 / 半导体设备 / 材料 / 设计 / 封装 / PCB / CPO / 光模块 / 算力 / AI应用 / 机器人 / 液冷
L3 个股（日）     选股 agent（不在本目录）
```

## 关键分级

| 名称 | 父赛道 | 档位 | 来源 |
|---|---|---|---|
| 存储芯片 | 半导体 | L2 主题级 | CSI 932580 + 东财 BK1137 + SW L3 850811（父级） |
| PCB | 元件 | L2 行业级 | SW L3 850822 + CSI 932666 + 东财 BK0877 |
| CPO | 通信设备/光通信 | L2 技术路线题材 | 东财 BK1128 + CSI 931723（光通信） |
| 人形机器人 | 自动化 | L2 题材 | CSI H30590 + 东财 BK1184 |
| 半导体设备/材料/设计/封测 | 半导体 | L2 行业级 | CSI 931743/932040 + SW L3 |

**注意**：CPO/PCB/存储不是 L1 赛道，不能和“半导体/元件/通信设备”并列；先赛道后细分。

## P0.5：从 seeds 推导，不手写代码

```bash
# 1) 只改语义种子/策略，不改代码
#    config/mainline/seeds.yaml
#    config/mainline/caps_policy.yaml

# 2) 重跑 resolver
python scripts/mainline_resolve_universe.py --date 20261023

# 3) 审计生成物
python scripts/mainline_p0_audit.py --fast --dir config/mainline/generated

# 4) 看 diff
cat config/mainline/generated/diff_report.md
```

- `seeds.yaml` 只写“叫什么、别名、层级意图、是否卫星、父赛道提示”，不写 `932580.CSI`/`BK1137.DC`。
- resolver 按名称/别名匹配 CSI/SW L3/东财/同花顺候选，按成员数、历史、父赛道重叠、纯度打分。
- 父赛道用“主题成员 ∩ 申万 L2 成员”的覆盖率推导，输出 `parents` 多父图；`parent_hint` 只在数据不足时纠正。
- caps 用 `caps_policy.yaml` + 成员数推导，并自动 clamp 到父 track 上限。
- **P0.5 生成物的 leader/ladder 仍用 P0 参照**（`leader_method: reference_pending_p1`），因为概念成员噪声会误选“寒武纪/中国电信”这类非核心；推导候选记录在 `resolution.derived_leader`，P1 加 core score（权重+RS+业绩+纯度）后接管。
- 生产读 `generated/`；手写 `universe_*.yaml` 降级为参照/回归。

## caps：只做项目级上限

每个 track/theme 都有 `caps`：

```yaml
caps:
  max_weight: 0.20          # 该项目在组合里的最大权重
  max_names: 2              # 该项目最多几只票
  max_single_stock: 0.15    # 单票上限
  max_probe: 0.05           # 试探仓上限
  max_add: 0.10             # 加仓上限
  cooldown_days: 3          # 止损后冷却
  allow_new: true           # 允许新开
  allow_add: true           # 允许加仓
  allow_base: true          # 允许底仓
  allow_t: true             # 允许做 T
```

- **不定义总仓位/现金**；总仓位由现有仓位函数决定。
- `max_single_stock` 必须 ≤ `max_weight`；`max_probe` 必须 ≤ `max_single_stock`。
- 卫星题材（资源/农业/金融/基建）：`max_weight ≤ 0.10`、`allow_base: false`，只做短差。
- 现有仓位函数读取这些 caps 时，按 `theme.id`/`track.id` 查表；本目录不实现仓位计算。

## 数据源

| 类型 | 代码示例 | 接口 | PIT |
|---|---|---|---|
| 申万 L2 | 801081.SI 半导体 | `index_classify(level='L2')` + `sw_daily` + `index_member_all` | ✅ in_date/out_date |
| 申万 L3 | 850811.SI 集成电路、850822.SI 印制电路板 | `index_classify(level='L3')` + `sw_daily` + `index_member_all` | ✅ |
| 中证主题 | 932580.CSI 存储芯片臻选、H30590.CSI 机器人、932666.CSI PCB | `index_basic(market='CSI')` + `index_weight` + `index_daily` | ⚠️ 权重按月/季 |
| 东财概念 | BK1137.DC 存储芯片、BK1128.DC CPO、BK0877.DC PCB | `dc_index` + `dc_member` + `dc_daily` | ❌ 当前成员 |
| 股票 | 603986.SH 兆易、688836.SH 宇树 | `stock_basic` / `daily` | — |

> 回测时：SW 用 `index_member_all` 的 `in_date/out_date`；CSI 用 `index_weight(trade_date)`；东财标 `membership_pit: false`，只做板块级/宽度回测，不做成分股级回测。

## P0.6 agent 复核（试验）

```bash
# 已保存 subagent 对 5 个歧义主题的复核结果
cat config/mainline/generated/agent_review.json

# 校验 + shadow 报告
python scripts/mainline_agent_review.py
# 0 errors, 1 warning（battery needs_human）

# 生成 shadow 应用版（不覆盖生产 universe_theme.yaml）
python scripts/mainline_agent_review.py --apply
# -> generated/agent_applied_theme.yaml
```

- agent 只做选择题：selected_sources / parent / leader / ladder / confidence / needs_human。
- 程序做守门：source 必须在候选、parent 必须存在、leader/ladder 必须在允许集合。
- 试验结论：
  * agent 修复核心：storage 兆易、PCB 胜宏、CPO 中际、humanoid 宇树、battery 宁德；
  * agent 去噪：CPO 弃 BK1128、battery 弃 BK1033、PCB 弃 BK0877、humanoid 弃 BK1184；
  * battery 因父赛道重叠 0、源无区分度，agent 主动 `needs_human=true`。
- 默认 shadow；`--apply` 写 `agent_applied_theme.yaml`，`--promote` 才替换生产。

### P0.6b 扩到 15 个主题

- 第二轮新增 10 个：semi_equipment、semi_material、chip_design、packaging、compute、ai_app、liquid_cooling、robot_actuator、satellite、space。
- 合并结果：15 themes / **0 errors / 6 warnings**（5 个 data_conflict + 1 个 ladder 不足）。
- 文件：
  * `generated/agent_review_expanded.json`
  * `generated/agent_review_expanded_validated.json`
  * `generated/agent_review_expanded_applied_theme.yaml`（已 promote）
  * `generated/universe_theme.before_agent.yaml`（promote 前备份）
- **用户 2026-10-27 确认：`needs_human` 直接确认，不再人工 gate。**
  * 新增 `review_policy.yaml`：`auto_confirm_needs_human: true`；程序仍保留 `data_conflict` 供审计。
  * 5 个 needs_human 全部确认：battery、packaging、compute、liquid_cooling、space。
- agent 的关键修正：
  * semi_material 弃 931743（材料+设备混合），用 850813 + BK1325，核心鼎龙；
  * chip_design 弃 932087 集成电路，用 850814 + 932040，核心兆易；
  * packaging 弃 derived 寒武纪，核心长电；
  * compute 弃 931656/BK1134，只用 931688，核心浪潮；
  * liquid_cooling 弃 derived 中兴，核心英维克；
  * robot_actuator 保留三花，satellite 保留中国卫星；
  * space 原名称池全是航空，已按 `857411.SI` 成员补纯航天核心（中国卫星/中国卫通/航天电子/航天环宇），不是把航发动力当航天核心。
- 已 promote 到生产 `generated/universe_theme.yaml`。
- 审计：`mainline_p0_audit.py --fast --dir generated` = **0 errors / 0 warnings / 42 items**。
- 后续 12 个主题继续复核时，`needs_human` 自动确认，只保留 `data_conflict` 审计位。

### P0.6c 全量 27 个主题

- 剩余 12 个已复核：passive、optical_module、communication_transmission、pv、optical_component、aviation、innovative_drug、medical_device、resources、agriculture、finance、construction。
- 结果：**0 errors / 9 warnings**（8 个 data_conflict + agriculture 保留现有 source）。
- 关键修正：
  * optical_module：931723 + BK1136，核心中际旭创；
  * communication_transmission：851024 + BK1592，核心亨通，明确不把中际旭创当传输核心；
  * pv：931151 + 857354，核心隆基；data_conflict（父赛道 overlap 0）；
  * optical_component：850833 + BK1334，核心水晶光电；不把京东方（面板）当光学元件核心；
  * aviation：857421 + BK1384，核心中航沈飞；
  * innovative_drug：931152 + 950161，核心恒瑞；data_conflict；
  * medical_device：H30217 + 851532，核心迈瑞；data_conflict；
  * resources：932256 + 850531，核心紫金；data_conflict；
  * agriculture：无合格候选源，保留现有 801017，核心牧原；data_conflict；
  * finance：851931 + 932133，核心中信证券；data_conflict；
  * construction：931931 + 932117，核心中国建筑；data_conflict。
- 已 promote：`generated/universe_theme.yaml`（27 themes 全部 agent reviewed）。
- 备份：`universe_theme.before_round3.yaml`（15 主题版）、`universe_theme.before_agent.yaml`（agent 前）。
- 审计：`mainline_p0_audit.py --fast --dir generated` = **0 errors / 0 warnings / 42 items**。

## 审计

```bash
# 快速：只查结构/代码存在/父子引用/caps
python scripts/mainline_p0_audit.py --fast

# 全量：加成员数/历史行数（会慢一些）
python scripts/mainline_p0_audit.py --date 20261023

# 输出 JSON
python scripts/mainline_p0_audit.py --json-out .dsh-tmp/mainline_p0_audit.json
```

审计通过标准：`0 errors`；warning 允许（新指数历史短、成员少等）。

## 变更流程

1. 新增/删除 track/theme：先改 YAML，再跑 `mainline_p0_audit.py`。
2. 新增代码必须能在 Tushare 查到（CSI/SW/东财）。
3. caps 变更：只改项目级，不引入总仓位/现金字段。
4. 规则变更：先套已知判例（10/23、10/21、09/02、07/02、05/09），误杀就回退。
5. 变更记录写入 `docs/mainline-universe-changelog.md`（P1 建）。


## P1：L0 月度主线 + L1 赛道周频

```bash
python jobs/mainline_monthly.py --date 20261023
python jobs/track_weekly.py --date 20261023
```

- 输出：`data/mainline/{as_of}/mainline.json`、`track.json`。
- P1 主评分不用 agent；agent 只做“月度主线切换”和“周频 top3 歧义”复核（默认 shadow），见 `docs/p1-agent-decision.md`。
- 2026-10-23 首跑：mainline=AI/科技，sub=AI/算力，regime=pullback，confirmed=false → track focus 降为 trial。
- P1 是机械评分/事实层，不是买卖信号；theme 日频在 P2。


## P2：L2 细分日频（theme_daily）

```bash
python jobs/theme_daily.py --date 20261023
python jobs/theme_daily.py --date 20261021
```

- 输出：`data/mainline/{as_of}/theme.json`。
- 评分：`0.20*z(ex1)+0.35*z(ex5)+0.20*z(breadth5)+0.15*z(leader_rs5)+0.10*z(amount_ratio)`。
- gate：主线未 confirmed → focus 降 trial；父赛道 avoid → focus 降 watch；卫星单独 focus/watch/avoid。
- P2 主评分仍不用 agent；27 主题的 agent 复核只用于“选源/核心/梯队”，日频分数是机械的。
- 2026-10-23：trial=人形机器人/机器人执行器/AI应用；存储/PCB/CPO/半导体设备 avoid。
- 2026-10-21：trial=人形机器人/卫星/机器人执行器；设备/PCB/材料 avoid（机械分数未确认 10/21 的“设备→PCB/材料”叙事）。


## P2.5：拥挤度 + caps + agent 复核

- `jobs/theme_daily.py` 已加：
  * 公募拥挤 `crowding_avg / crowding_max / crowding_penalty`（用 `data/fund_holdings/aggregate.json`，只取 `latest_end_date <= as_of`，避免未来数据）；
  * 每行输出 `caps`（现有仓位函数直接读，不新增总仓位函数）。
- 2026-10-23 拥挤惩罚示例：
  * 人形机器人 crowd_avg 1.58 / max 4.64 → penalty +0.146；
  * 存储 crowd_avg 1.31 / max 6.19 → penalty +0.079；
  * PCB crowd_avg 0.273 / max 0.77 → penalty -0.181（低拥挤反而加分）。
- agent 复核（10/23，7 个主题）：
  * `scripts/theme_agent_review.py` 校验 verdict/置信度；
  * 结果：humanoid_robot/robot_actuator = trial；medical_device/optical_component/ai_app = watch；storage/pcb = avoid；
  * 机械与 agent 完全一致，0 errors，`theme_agent_applied.json` 已生成（shadow）。
- 待做：09/02、07/02、05/09 三个判例的权重/window 校准；P2.5 完成后再接仓位。


### P2.5 校准发现（2026-10-27）

- 修了两个日期 bug：
  * 非交易日 as-of 现在回退到上一个交易日（05/09 → 05/08）；
  * `trade_dates` 窗口从固定 20260701 改为 as-of 前 150 天，07/02、05/08 的 5d/20d 不再失真。
- 同日 top3 命中低（10/23 2/5、10/21 0/4、09/02 0/2、07/02 0/2、05/08 0/1），但**判例是未来方向，不是当天涨幅**，同日排名是错误指标。
- 前向 T+5 校准（有数据的 3 个 case）：
  * 05/08 存储 +0.0%，机械 top 航天 -9.8%、机器人执行器 -1.1%；
  * 07/02 存储 +1.4%、半导体材料 -8.5%，机械 top 创新药 -1.7%、航天 -2.2%；
  * 09/02 半导体材料 -1.2%、存储 -0.2%，机械 top 航空 +2.0%、液冷 +0.4%、AI 应用 +0.9%。
- 结论：5 个 case 不足以调权重；机械 top3 有时优于判例主题、有时更差，**不做权重过拟合**。下一步做 20-30 个前向 case，再校准。
- agent 10/23 复核 7 个主题：全部维持 trial/watch/avoid，0 errors；仍是 shadow。


### P2.5b 前向权重网格（21 cases）

- 新增 `scripts/theme_forward_eval.py`：从 `docs/wolf-vs-system-compare.md` 取 2026 判例日，映射到 27 主题，用主 source 的 1d/5d/20d/amount 做网格，评估 top3 命中与 T+5 超额。
- 结果（21 cases）：
  * 当前权重（0.2/0.4/0/0.1）hit3 = **0.38**，forward +1.53%；
  * 最佳 hit3 = **0.67**（w1=0, w5=0.2, w20=0.2, wa=0.1），forward +0.81%；
  * 最佳 forward = **+1.69%**（w1=0, w5=0.4, w20=0.1, wa=0）。
- 结论：**加 20d、去掉 1d** 能明显提高 top3 命中；不追求单点最高 forward，避免过拟合。
- 已落 `config/mainline/theme_weights.yaml`：
  `ex1=0.00, ex5=0.30, ex20=0.20, breadth=0.25, leader_rs=0.15, amount_ratio=0.10`。
- 10/23、10/21 用新权重重跑：trial 仍是机器人组，10/21 航天升到 trial；存储/PCB 仍 avoid。

### P2.5c 日期口径修复

- 非交易日 as-of 回退上一交易日：05/09 → 05/08。
- `trade_dates` 窗口改为 as-of 前 150 天，07/02、05/08 的 5d/20d 不再失真。
- 05/08 前向：存储 +0.0%，机械 top 航天 -9.8%、机器人执行器 -1.1%。
- 07/02 前向：存储 +1.4%、半导体材料 -8.5%，机械 top 创新药 -1.7%、航天 -2.2%。
- 09/02 前向：半导体材料 -1.2%、存储 -0.2%，机械 top 航空 +2.0%、液冷 +0.4%、AI 应用 +0.9%。


### P2.5c 95-case holdout 与双 profile

- 样本：95 个判例日；train 76（~2026-01~06），holdout 19（2026-06-30~08-14）。
- 结果：

| 权重 | train hit3 | train T+5 | holdout hit3 | holdout T+5 |
|---|---:|---:|---:|---:|
| default 0.2/0.4/0/0.1 | 0.43 | +2.51% | 0.26 | **-2.03%** |
| accuracy 0/0.3/0.2/0.1 | 0.51 | +2.98% | **0.53** | -3.15% |
| other 20d variants | 0.53-0.55 | +2.8~3.0% | 0.53 | ~-3.0% |

- 结论：加 20d 明显提高 top3 命中，但 holdout T+5 超额变差；**没有单一大满贯权重**。
- 处理：
  * 生产默认保留 `theme_weights.yaml`（default，偏 T+5）；
  * 另存 `theme_weights_accuracy.yaml`（accuracy，偏 top3 命中），研究/切换用；
  * 不做自动 promote。
- 已给 `track.json` 加 `caps` 和 `project_caps`；theme 的 caps 已在 `theme.json`。
- 下一步：按 regime 分段（up/pullback）比较 default/accuracy，再决定是否动态切换。


### P2.5d 判据匹配（5 天持有）

用户修正：**P2.5 不是收益率优化，先把判据匹配做对；持仓 5 天，按 5 天口径校验。**

`theme_daily.py` 现在输出：

- `holding_days: 5`
- 每主题 `criteria` / `criteria_pass` / `criteria_total`：
  * `mainline_ok`：主线 confirmed；
  * `parent_ok`：父赛道不是 avoid；
  * `leader_ok`：leader 5d RS > 0；
  * `breadth_ok`：5d 宽度 ≥ 50%；
  * `amount_ok`：5d/20d 成交额比 ≥ 1；
  * `trend_ok`：20d 收益 > -10%；
  * `crowding_ok`：crowding_avg ≤ 2.0。
- Gate：focus/trial 必须 `criteria_pass >= 4`；卫星 focus 同样受此约束。
- 校验脚本：`scripts/theme_criteria_check.py --dates 20261023,20261021`。

10/23、10/21 结果：

- 10/23：trial = 人形机器人、机器人执行器；satellite 空；
- 10/21：trial = 人形机器人、机器人执行器、航天；satellite 空；
- `theme_criteria_check.py` = **0 errors**。

说明：
- `theme_forward_eval.py` 只作诊断，**不用于调生产权重**；
- 生产保留 `theme_weights.yaml`（default）；
- `theme_weights_accuracy.yaml` 仅研究，不自动切换；
- 5 天持有期内，按 `criteria_pass` + 父赛道/主线 gate 判断是否继续持有。


### P2.5e 5 天持有：只输出判据有效性

`config/mainline/holding_policy.yaml` 只保留：

```yaml
holding_days: 5
criteria_pass_min: 4
review_on_fail: true
```

`theme_daily.py` 每行输出：

- `thesis_valid`：`criteria_pass >= 4` 且 `parent_ok` 且 `trend_ok`；
- `review_required`：`thesis_valid=false` 或主线 regime=weak；
- `failed_criteria`：不满足的判据列表。

**hold/reduce/exit 由现有仓位函数决定，本层不定义仓位动作。**

5 个 case：

| 日期 | focus | trial | 说明 |
|---|---|---|---|
| 10/23 | 空 | 人形机器人、机器人执行器 | 主线 pullback，卫星空 |
| 10/21 | 空 | 人形机器人、机器人执行器、航天 | 1 个 DC 源缺失 |
| 09/02 | 空 | 航空装备、液冷、AI 应用 | DC 源有缺口 |
| 07/02 | 空 | 创新药、航天 | 1 个 DC 源缺失 |
| 05/08 | 航天、机器人执行器 | 空 | 主线 confirmed=true |

`theme_holding_check.py` = **0 errors**；`theme_criteria_check.py` 5 case = **0 errors**。


### 关于 09/02、07/02 的“机器人执行器/液冷缺失”

- **不是历史数据不存在**。直接查 `dc_daily`：
  * BK1145.DC（机器人执行器）193 行，20260702/20260902 都有；
  * BK1100.DC（减速器）193 行，日期齐全；
  * BK1138.DC（液冷服务器）193 行，日期齐全。
- 真实原因：多日期循环里 `dc_daily` 偶发限流/400/502，`theme_daily` 当时对单个 source 失败直接 `continue`；机器人执行器两个 source 都是东财，两个都失败就“no source data”；液冷只有一个东财源，失败即无数据。
- 复现：单独重跑 09/02、07/02 已不再报错。
- 修复：
  * `fetch_series` 对每个 source 重试 3 次 + 缓存；
  * 失败写入 `source_errors`，不再静默丢弃；
  * 后续 P2.6 再给纯东财主题加 SW/CSI 代理兜底。


### P2.6 接到现有仓位函数读取层

新增 `backend/app/services/mainline_read.py`：

- 读 `data/mainline/{as_of}/theme.json` / `track.json` / `mainline.json`；
- 支持 `theme_id` / `track_id` 显式指定，或按 symbol 在 theme/track 的 leader/ladder 里查找；
- 返回 `thesis_valid` / `review_required` / `failed_criteria` / `caps` / `holding_days`。

`POST /api/v1/indicator/calc-position` 已接入：

- 请求新增可选 `theme_id`、`track_id`；
- 响应新增 `mainline`（只读上下文）；
- caps 只压单票/试探/加仓：
  * `max_single_stock` → 单票上限；
  * `max_probe` → 试探仓上限；
  * `max_add` → 加仓上限；
  * `allow_new` / `allow_add` / `allow_t` 为 false 时禁止对应 intent；
- `thesis_valid=false` / `review_required=true` 只加 warning，不直接改仓位；
- **不新增总仓位函数，不改总仓/现金。**

测试：

```bash
python -m pytest backend/tests/test_mainline_read.py -q
# 3 passed
```

说明：同一股票可能属于多个主题（如兆易同时是 storage/chip_design 的 leader），按 symbol 查会优先非 avoid 的主题；需要精确时传 `theme_id`。


### P2.7 调用方带 theme_id + 前端展示 mainline

- `CalcPositionRequest` 已支持 `theme_id` / `track_id`。
- 自动调用方已在 `calc_position` 前解析 context：
  * `candidate_pool_monitor._execute_buy`；
  * `long_term_pool_monitor._execute_buy`。
- Agent/工具：
  * `frontend/src/utils/toolRegistry.ts` 的 `calc_position` 增加 `theme_id` / `track_id` 参数；
  * `docker/dsh/bridge/lib/index.js` 的 `calc_position` 同步透传。
- 前端 `ChatContainer.tsx` 为 `calc_position` 注册专用 renderer：
  * 在通用 JSON 上方显示 `📌 主题 · verdict · thesis_valid · review_required`。
- 构建/测试：
  * `cd frontend && npm run build` ✅；
  * `pytest backend/tests/test_mainline_read.py -q` → 3 passed。


## P3 个股层（第一版）

### 个股候选

```bash
python jobs/stock_select.py --date 20261023 --max 8
# -> data/mainline/20261023/stock_candidates.json
```

- 只从 `theme.json` 的 `focus/trial` 主题出发；
- 取主题 YAML 的 leader/ladder，列 5 日涨幅、收盘、市值、主题 verdict、thesis_valid、caps；
- 不替用户下单。

10/23 候选（trial 主题：人形机器人、机器人执行器）：

| 主题 | 角色 | 代码 | 名称 | 5日涨幅 |
|---|---|---|---|---:|
| 人形机器人 | leader | 688836.SH | 宇树科技 | +46.1% |
| 人形机器人 | ladder | 003021.SZ | 兆威机电 | +50.1% |
| 人形机器人 | ladder | 688017.SH | 绿的谐波 | +22.5% |
| 人形机器人 | ladder | 601689.SH | 拓普集团 | +12.5% |
| 人形机器人 | ladder | 002050.SZ | 三花智控 | +11.7% |
| 机器人执行器 | ladder | 603728.SH | 鸣志电器 | +35.4% |
| 机器人执行器 | ladder | 603667.SH | 五洲新春 | +17.3% |
| 机器人执行器 | ladder | 002979.SZ | 雷赛智能 | +11.2% |

### 5 天持仓复核

```bash
python jobs/holding_review.py --symbol 688836.SH --days 5
# -> data/mainline/{as_of}/holding_review_688836_SH.json
```

只列每天涨幅，不输出 hold/reduce/exit，由用户判断：

```text
date       close   pct_chg   cum
20261021   515.0   -2.10%   -2.10%
20261022   520.0   +0.97%   -1.15%
20261023   624.0   +20.00%  +18.62%
20261026   672.0   +7.69%   +27.75%
20261027   639.96  -4.77%   +21.66%
context: 人形机器人 | verdict=trial | thesis_valid=True | review_required=False
```

**持仓复核只给数据，不给买卖结论。**


## P3 选股逻辑审计

- 见 `docs/p3-stock-selection-audit.md`。
- 之前 `stock_select.py` 的问题：把 theme YAML 的 leader/ladder 当核心/龙头、按 5 日涨幅排序、把主题 verdict 当个股许可、max 8 自设上限。
- 已改为 **facts-only**：不排序、不把 leader 叫核心、只给 1d/5d/20d、20 日高低距离、成交额/换手/市值排名/拥挤/主题 caps。
- 分类（核心/风向标/龙头/龙2/杂毛）留给 agent/用户，下一步按 B1 原话做分类层，不用单因子。


### P3 agent 选股试验（2026-10-23）

- 候选池来自主题 **source 成员**（人形 72 / 执行器 106），不再只用 leader/ladder；
- agent 按狼大分层（核心/风向标/龙头/龙2/二供/杂毛）+ 高位/低位规则选：
  * 人形机器人：三花（核心/probe）、宇树（风向标/watch）、大族（杂毛/reject）；needs_human=true（营收拐点缺失）。
  * 机器人执行器：拓普（二供/probe）、绿的（龙头/watch）、山子（杂毛/reject）；needs_human=false。
- 校验：`scripts/stock_agent_review.py` → 0 errors；只允许候选内代码。
- 文件：`stock_agent_facts.json` / `stock_agent_review.json` / `stock_agent_validated.json` / `stock_agent_selected.json`。


### 10/28 盘中数据保护（2026-10-28 09:54）

- `daily(20261028)` 返回 0 行（盘中未收盘），`track_weekly`/`theme_daily`/`stock_select` 已加 guard：
  `[data_warning] 20261028 日线未生成（盘中/未收盘），请用上一交易日或 15:00 后重跑`，exit 2。
- 10/28 15:00 后重跑：
  ```bash
  python jobs/mainline_monthly.py --date 20261028
  python jobs/track_weekly.py --date 20261028
  python jobs/theme_daily.py --date 20261028
  python jobs/stock_agent_facts.py --date 20261028 --top 15
  python jobs/stock_select.py --date 20261028 --max 12
  # 再对 stock_agent_facts.json 跑 agent 选股 + scripts/stock_agent_review.py
  ```

### 10/27 完整流程（最后完整交易日）

- mainline：AI/科技，AI/算力 -19.24%，半导体/芯片 -21.03%，`regime=weak`，`confirmed=false`。
- track trial：电气自动化设备、计算机应用、光学光电子；focus 空。
- theme trial：人形机器人、机器人执行器；watch：电池/医疗器械/光学元件/光伏/AI 应用。
- stock facts：人形 72 / 执行器 106 成员，候选 16/17；`stock_candidates.json` 8 只事实。
- 文件：`data/mainline/20261027/{mainline,track,theme,stock_agent_facts,stock_candidates}.json`。


### P3 agent 选股 10/27（补跑）

- 10/27 首轮只到 facts，**没调 agent**；现已补调并校验。
- 人形机器人：绿的谐波（二供/probe）、汇川技术（核心/watch）、石头科技（杂毛/reject）。
- 机器人执行器：三花智控（核心/probe）、长盈精密（风向标/watch）、山子高科（杂毛/reject）。
- 与 10/23 差异：10/27 `regime=weak`，宇树换手 23% 过热不再给 probe；改为回调充分的绿的谐波二供；长盈已 +48% 只作风向标。
- 校验：`scripts/stock_agent_review.py --date 20261027` = 0 errors。
- 文件：`data/mainline/20261027/stock_agent_{review,validated,selected}.json`。


### 主线 confirmed 为什么少（2026-10-27 诊断）

- 23 个采样日：confirmed **34.8%**；月度：06 60%、07 0%、08 20%、09 80%、10 0%。
- 当前 10 月不是“规则没跑”，是 **AI/科技本来就在跌**：
  * 10/27 AI/算力 20d **-26.87%**，半导体/芯片 **-28.67%**，上证 **-7.64%**；
  * 绝对收益为负、相对超额也为负 → `mainline_exists=true, tradeable=false, status=weak`，不是缺数据。
- 规则/基准偏严：
  * `confirmed` 要求“20d 超额 > 0”，基准是上证；AI 绝对上涨 43.5% 的日子里，只有 47.8% 天超额为正；
  * 绝对涨但跑输上证 → `status=pullback`、`tradeable=false`；主线仍在，只是不可交易。
- 数据缺口：
  * 10/27 `931656/931657` 无数据，AI 代理覆盖 2/4=0.5；
  * 08/18 `932580` 只有 16 行，覆盖 0.75；
  * 旧逻辑 coverage<0.8 直接把 confirmed 判 false，和数据缺口混在一起。
- 已加字段：`mainline_exists`（配置主线存在）、`mainline_active`（市场当前活跃主线）、`tradeable`、`status`（confirmed/pullback/weak）、`abs_ok`、`rel_ok`、`cov_ok`、`data_warning`；不再让 coverage 单独决定 confirmed。
- 10/27 输出：`mainline_exists=true, tradeable=false, status=weak`；`track/theme` 同步带 `mainline_exists/mainline_tradeable/mainline_status`。


### P3 事实字段：60/120 日位置 + ST/业绩 + 老龙头

`jobs/stock_agent_facts.py` 现在每只候选带：

- 位置：`ret60/ret120`、`dist_high60/dist_low60`、`dist_high120/dist_low120`、`range_pos_120`；
- 风险：`is_st`、`list_status`；
- 业绩：`earnings{end_date,revenue,yoy_revenue,n_income,yoy_n_income,announce_date,status}`，status=ok/loss/missing；
- 老龙头：`leader{seed_role, limit_up_120d, top3_days_120_pool, pool_size}`。

注意：`top3_days_120_pool` 是**候选池内**涨幅前 3 的次数，不是全主题；全主题口径需补全成员历史。


### P3 高位/低位规则包

- `config/mainline/position_rules.yaml`：high/low/mid 阈值 + 两套规则；
- `scripts/position_rules.py`：个股位置分类；
- `stock_agent_facts.py` 每只候选新增 `position`：
  * `state`: high/mid/low；
  * `entry_mode`: no_chase / ambush_candidate / neutral / no_entry；
  * `hold_bias`: 卖强留弱 / 留强丢弱 / 等确认；
  * `evidence`、`risk_flags`、`leader_evidence`、`threshold_source`。

阈值初值沿用 `position_class.py`，标记“待判例校准”；`top3_days_120_pool` 仍是候选池口径。
10/27 结果：人形/执行器多数 `low / ambush_candidate`；科大讯飞 `mid / neutral`；无 high。


### 低位埋伏必须过浪型/主线 gate

- `position_rules.py` 现在同时读 `data/wave_state.json` 的 `operation` 和当日 `mainline_active`；
- `low` 只有在 **mainline_active=true 且 P3 允许 new_base/probe** 时才给 `ambush_candidate`；
- `defense` / `exit` 或主线不 active → `watch`；
- `high` → `no_chase`；defense/exit 时 `hold_bias=卖强减仓`；
- 10/27：wave=defense、mainline_active=false → 所有 low 都是 `watch`，不再出低位埋伏。


### P3 筹码/席位/股东字段

`stock_agent_facts.py` 每只候选新增：

- `chip`：`cyq_perf` 的 winner_rate、cost_5/15/50/85/95、weight_avg、concentration、price_vs_cost50/85；
- `holders`：`stk_holdernumber` 最新/上期股东户数、trend；
- `seats`：最近 20 个交易日 `top_inst` 龙虎榜席位，inst_net/hot_money_net/list_days；
- `block_trade`：最近 180 天大宗交易 count/amount/avg_price_vs_close/inst_buy/inst_sell。

口径：cyq 是模型估算；top_inst 只有上榜日；block_trade 180d；不是“筹码干净”布尔。


### P3 候选池扩展与 PIT

- `stock_agent_facts.py` 默认 `--top 18`；`--top 30` 扩到每主题 30；`--full` 使用全部成员（10/27：人形 72 / 执行器 106）。
- 业绩快报按 `ann_date <= as_of` 过滤，避免未来数据；`announce_date` 已返回。
- 筹码/股东/大宗/席位都按 as_of 日期取。
- 注意：`--full` 会产生较大 facts 和较多 API 调用；agent 选股时建议先 `--top 30`，需要全量再开。
