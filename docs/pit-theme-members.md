# PIT 主题成员历史（core P1）

> 2026-10-21 · `jobs/build_pit_theme_members.py` v1.0
> 产物：`data/pit/theme_members/`（本地生成，`data/` 已被 `.gitignore` 忽略，不做 data sync）
> 结论先说：**本地没有完整的 point-in-time 概念成分表**；2025 年只有 2025-08-20 之后的稀疏
> 日频证据。本实现只输出“有日期证据”的成员，并把当期快照单独标成 `pit=false`，不混入 as-of 结果。

## 1. 一句话结论

| 问题 | 答案 |
|---|---|
| 2025 年有完整 PIT 成员吗？ | **没有**。`concept_hist.json` 从 2025-08-20 才开始，且每概念每天只有 1 只 leader。 |
| 2025-08-20 ~ 2026-08-31 有什么？ | 37,760 条日频证据（34,280 `fresh` + 3,480 `ffill_repeat`）：某股票是某概念的 `buy_sm_amount_stock`，即属于该概念。 |
| 2026-09-17 ~ 2026-10-15 有什么？ | 8 个日期化 `chain_map_*.json` 的候选/核实成员，共 5,981 条；更早的 20 个 chain_map 被拒收（会引入未来成分）。 |
| 当期快照能用吗？ | 只能作为“当前宇宙”，**不能**用于历史 as-of；单独文件 `current_snapshot_not_pit.csv`，`pit=false`。 |
| 覆盖多少主题？ | 14 个父主题全部有数据；49 个子主题中 32 个有数据、17 个为 0（概念名在 `concept_hist` 里不存在）。 |
| 总规模？ | 43,741 条观测、2,293 只股票、258 个日期；14 项 leak tests 全过。 |

## 2. 数据盘点：哪些是 PIT，哪些只是当期快照

| 来源 | 实际覆盖 | 粒度 | PIT? | 本实现是否使用 |
|---|---|---|---|---|
| `data/concept_hist.json` | 521 概念 × 250 交易日，`20250820`~`20260831` | 每概念每天 `leader`（Tushare `moneyflow_ind_dc().buy_sm_amount_stock`）、`close`、`net_amount` | **是（日频观测）**，但每概念每天仅 1 只，且生成端对缺失日做过 ffill | ✅ 核心 as-of 证据 |
| `data/stock_pool.db::stock_concept_map` | 5,551 只股票 / 108,648 条概念关系 / 1,061 个概念 | 当前快照，无 `valid_from/valid_to` | ❌ 当期快照；`max(updated_at)=2026-08-03`，文件 mtime `2026-09-16` | ✅ 仅用于股票名→代码映射 + 显式非 PIT 快照 |
| `data/chain_map_YYYYMMDD.json` | 28 个日期文件，`20260615`~`20261015` | 主题/环节的 `candidates`、`leading_verified`、`verified_all`、`ai_promoted` | **半 PIT**：文件有日期，但成员是构建时用 `stock_concept_map` 现查的；早于 DB 快照日期就会把未来成分带回历史 | ✅ 仅用 `>= 20260916` 的 8 个日期 |
| `data/_bt_t1/*/stock_pool.db`、`data/_bt_year/*`、`data/_bt_mar/*` 等 | 10,500 个 `stock_pool*.db` 路径 | 日期目录里的 DB | ❌ **不是历史快照**：10,424 个是软链；48 个完整副本全部同一 `stock_concept_map` 哈希 `d5950ab264`、`updated_at=2026-08-03`；28 个 1.1MB 旧 paper-trading 副本全部同一哈希 `09ede332ae`（2,405 股票/89 概念，`updated_at=2026-05-06`），不是主题成员表 | ❌ 未使用 |
| `data/theme_breadth_daily.parquet` | 13 主题 × 476 日期，`20241101`~`20261020` | 每日 `n_stocks/breadth` | ❌ 每个主题的 `n_stocks` 在所有日期恒为同一个值（`nunique=1`）⇒ 不是逐日成员变化；很可能是固定宇宙/当期表回填 | ❌ 未使用 |
| `data/sw_members_cache.json` | 5,918 行申万行业成员，含 `in_date/out_date` | 行业（L1/L2/L3）级历史成员 | **是（行业级）**，但不是概念/主题成员 | ❌ 未使用（没有可辩护的主题→行业映射） |
| `data/concept_long.json` | 169 概念，`20250101`~`20260915` | 每概念等权收益序列；`meta.members=4,846` 是全局计数 | ❌ 无逐概念成员名单 | ❌ 未使用 |
| `data/chain_contagion.json` | `20260914` 单日 | 链级 anchors 6 只/环节 | 仅锚点，不是成员表 | ❌ 未使用 |
| PG `daily_artifacts` / `data/_bt_pg` | — | 按日产物 / PG 数据目录 | 本机不可达/不可读 | ❌ 未使用（见 §8） |

### 2.1 关键审计：日期目录不是历史快照

实测（`find data -name 'stock_pool*.db'`）：

- 10,500 个路径；
- 10,424 个是软链（`_bt_t1`、`_bt_year` 等），最终指向 `data/stock_pool.db`；
- 48 个完整 10.85MB 副本（`_bt_mar` 23 个日期 × 2 + 当前 2 个）全部同一成员表哈希 `d5950ab264`；
- 28 个 1.1MB 旧 `apps/paper-trading/data/stock_pool.db`（`_bt_code` / `_bt_code_year` 代码快照内）全部同一哈希 `09ede332ae`：2,405 只股票 / 89 个概念 / `updated_at=2026-05-06`，是旧的纸交易子集，不是主题成员历史。

因此：**不能**拿 `data/_bt_t1/20260105/stock_pool.db` 当“2026-01-05 的成分表”。它只是当前表的软链。

### 2.2 关键审计： `theme_breadth_daily.parquet` 不能当逐日成员表

13 个主题的 `n_stocks` 在 476 个日期上各自只有一个唯一值（例如 AI/算力/科技恒为 1096）。
这说明它不是逐日成分变化的结果（可能是固定宇宙，也可能是当期成员表回填）；无论哪种，
它都不提供历史成员名单。本实现不使用它。

## 3. as-of 逻辑

### 3.1 观测字段

`observations.csv` 每行一条证据：

| 字段 | 含义 |
|---|---|
| `as_of_date` | 这条成员关系被断言/观测的日期 |
| `theme` / `theme_level` / `parent_theme` | 主题名、`parent`/`subtheme`、父主题 |
| `ts_code` | 股票代码（`000001.SZ` 形式） |
| `concept_name` | `concept_hist` 行 = 概念名；`chain_map` 行 = `segment:<环节label>`（只证明“在该环节里”，不冒充该股票属于环节内每个概念） |
| `source` | `concept_hist.leader` 或 `chain_map.candidates/leading_verified/verified_all/ai_promoted` |
| `source_date` | 证据首次出现的日期；永远 `<= as_of_date` |
| `observation_type` | `fresh`（当天新观测）/ `ffill_repeat`（生成端前值填充）/ `dated_artifact`（日期化产物） |
| `quality` | `high` / `medium` / `low`（按 fresh、run 长度、概念覆盖天数降级） |
| `notes` | 概念覆盖天数、leader run 长度、concept code、chain_map 文件名等 |

### 3.2 `concept_hist` 的 leader 为什么能当 PIT 成员

Tushare `moneyflow_ind_dc(trade_date=D, content_type="概念")` 的 `buy_sm_amount_stock` 是
**D 日该概念里小单买入金额最大的股票**。它能出现在这个字段里，就证明 D 日它是该概念的成分股。
这是“1 概念 × 1 天 = 最多 1 只”的稀疏证据，不是完整成分表，但**不是未来函数**。

生成端 `build_concept_matrix.py` 对有缺口的日期做了 `s_ldr.ffill()`，所以同一 leader 可能连续出现很多天。
本实现：

- 只在 leader 变化的第一天标 `fresh`；
- 后续标 `ffill_repeat`，`source_date` 指回 run 的第一天；
- `--max-staleness-days` 默认 **0**：查询只用当天证据；要“成员延续”必须显式传 >0 的窗口。

### 3.3 `chain_map` 的日期门槛

`apps/main_line/chain_map.py` 构建时无条件读 `data/stock_pool.db::stock_concept_map`。
所以 `chain_map_20260615.json` 里的“成员”可能是 2026-08-03 才有的成分。

本实现默认 `--snapshot-policy strict`：

```
gate_date = max(stock_pool.content_date=20260803, stock_pool.file_date=20260916) = 20260916
```

只接受 `date >= gate_date` 的 chain_map。实际：

- 28 个日期文件；
- 8 个通过：`20260917, 20260923, 20260924, 20260928, 20261009, 20261012, 20261013, 20261015`；
- 20 个拒收：`20260615`~`20260915`，manifest 的 `inputs.chain_map.excluded` 逐条列出。

> `--snapshot-policy content` 可把门槛放宽到 `20260803`，会多收 2026-08-04~09-15 的
> 15 个文件；但若 `stock_concept_map` 实际是 9 月才同步的，这些日期就会漏未来成分。
> 默认不这么做。

### 3.4 当期快照隔离

`current_snapshot_not_pit.csv`：

- 19,492 行，覆盖 14 父主题 + 49 子主题；
- 每行 `pit=false`；
- `note` 明确写 “NOT point-in-time; do not use for historical as-of”；
- 校验会拒绝任何 `source=current_snapshot*` 的行进入 `observations.csv`。

## 4. 覆盖

### 4.1 日期

| 区间 | 日期数 | 来源 |
|---|---:|---|
| 2025-08-20 ~ 2026-08-31 | 250 | `concept_hist.json`（真日频 leader） |
| 2026-09-17 ~ 2026-10-15 | 8 | 通过 gate 的 `chain_map_*.json` |
| **合计** | **258** | 2025-08-20 之前无 PIT 成员证据 |

### 4.2 父主题（14/14 有数据）

“日成员数”指当天**已知**成员数，不是主题完整成分数；括号内为 chain_map 日期的范围。

| 主题 | 匹配概念 | 缺失概念 | concept_hist 日成员 min/median/max | chain_map 日成员 | 唯一股票 |
|---|---:|---|---:|---:|---:|
| AI/算力/科技 | 22 | — | 7 / 13 / 18 | 32–32 | 512 |
| 传媒/游戏 | 6 | — | 2 / 5 / 6 | 20–20 | 196 |
| 军工/航天 | 6 | 军工电子Ⅱ、航天装备Ⅱ、航空装备Ⅱ | 1 / 4 / 6 | 40–40 | 261 |
| 农业 | 8 | 渔业、生猪养殖、种子、粮食种植、肉鸡养殖、饲料 | 3 / 6 / 8 | 47–48 | 206 |
| 医药 | 10 | 化学制剂、医疗研发外包、医疗耗材、医疗设备、生物制品 | 4 / 8 / 10 | 79–82 | 332 |
| 半导体/芯片 | 10 | 半导体材料、半导体设备、数字芯片设计、模拟芯片设计、集成电路制造、集成电路封测 | 4 / 7 / 10 | 59–60 | 276 |
| 新能源/电池 | 6 | 电池化学品、锂电专用设备 | 1 / 3 / 6 | 58–58 | 253 |
| 机器人/智能制造 | 7 | 其他自动化设备、工控设备、机床工具、激光设备 | 4 / 6 / 7 | 60–60 | 279 |
| 汽车/智驾 | 10 | 底盘与发动机系统、汽车电子电气系统、汽车零部件 | 5 / 8 / 10 | 78–80 | 313 |
| 消费/内需 | 8 | — | 2 / 4 / 8 | 25–26 | 237 |
| 电力/公用 | 5 | 光伏发电、核力发电、水力发电、火力发电、电网自动化设备、综合电力设备商、风力发电 | 2 / 5 / 5 | 75–76 | 256 |
| 稳增长/基建 | 4 | 国际工程、基建市政工程、水泥制造、涂料、玻璃玻纤、装修建材、防水材料 | 1 / 3 / 4 | 45–46 | 177 |
| 资源/周期 | 3 | 工业金属、能源金属、贵金属、钨、钼、铅锌、铜、铝、锂 | 2 / 3 / 3 | 47–47 | 126 |
| 金融 | 5 | 保险、垂直应用软件、证券Ⅱ、软件开发、银行 | 1 / 4 / 5 | 57–58 | 215 |

> “缺失概念”是配置里有、但 `concept_hist` 的 520 个概念名里不存在的概念；不是没抓到数据，
> 而是这些概念不在日频资金流概念池里（大量是行业/细分行业概念）。

### 4.3 子主题（32/49 有数据）

有数据（32）：AI软、光模块/CPO、半导体设备、半导体材料、IDC/算力租赁、AI硬件/PCB/液冷、
AI芯片、存储芯片、第三代半导体、人形机器人、工业机器人、创新药、中药、医疗器械、核电、绿电、
航空装备、卫星互联网、船舶、锂电池、储能、汽车整车、汽车零部件、汽车电子、黄金、稀土、养殖、
基建、游戏、影视、白酒、银行（仅 chain_map 8 个日期）。

零覆盖（17）：`CXO`、`保险`、`光伏`、`军工电子`、`地产`、`家电`、`封测`、`水电`、`火电`、
`煤炭`、`电网设备`、`种植业`、`航天装备`、`芯片设计`、`证券`、`风电`、`食品饮料`。

原因：这些子主题的概念名（如 `光伏`、`煤炭`、`证券`）不在 `concept_hist` 的概念池里；
`银行` 仅靠 chain_map 的 8 个日期有数据，仍无日频 PIT 覆盖。不伪造、不用当前快照回填。
可查 `theme_summary.csv` 的 `matched/missing_concepts`。

### 4.4 观测类型

| 类型 | 行数 | 含义 |
|---|---:|---|
| `fresh` | 34,280 | 当天新观测（高置信） |
| `ffill_repeat` | 3,480 | 生成端前值填充的 repeat，`source_date` 指回 run 起点 |
| `dated_artifact` | 5,981 | 通过 gate 的 chain_map 日期化成员 |
| **合计** | **43,741** | 2,293 只唯一股票 |

## 5. 校验 / leak tests

构建器内置 14 项校验，实际结果 **14/14 PASS**（`validation.json`）：

| 校验 | 结果 |
|---|---|
| `observations_nonempty` | 43,741 行 |
| `as_of_date_format` / `source_date_format` | 0 个坏日期 |
| `no_future_source_date` | 0 行 `source_date > as_of_date` |
| `chain_map_gate_strict` | 0 行 chain_map 早于 `20260916` |
| `ts_code_format` | 0 个坏代码 |
| `theme_known` | 0 个未知主题 |
| `theme_level_known` | 0 个未知层级 |
| `parent_theme_consistent` | 0 个父子主题不一致 |
| `no_current_snapshot_in_pit` | 0 行当期快照混入 |
| `current_snapshot_marked_not_pit` | 19,492 行全部 `pit=false` |
| `no_duplicate_observation_keys` | 0 个重复 `(date, theme, ts, concept)` |
| `coverage_matches_observations` | 16,254 行 coverage 与 observations 完全对账，0 mismatch |
| `row_counts` | 与 manifest 计数一致 |
| `pytest` | `tests/test_pit_theme_members.py` 7 passed |

### 5.1 leak tests 具体做了什么

1. **未来 source 检查**：所有观测必须 `source_date <= as_of_date`；`ffill_repeat` 的
   `source_date` 指回 run 起点，不会出现“用明天/下月的 leader”。
2. **chain_map 日期门槛**：`date < gate_date` 的文件一条都不读；manifest 记录拒收清单。
3. **快照隔离**：`observations.csv` 不允许出现 `current_snapshot*` source；当前快照文件
   每行 `pit=false`。
4. **对账**：按 `(theme, as_of_date)` 重算唯一股票/概念数，必须等于 `coverage.csv`。
5. **as-of 查询测试**：请求日之后的证据不会进入结果；`max_staleness_days=0` 时只用当天证据。
6. **真实数据 smoke test**：实际跑构建器到临时目录，断言 14 父主题、49 子主题、
   `validation.passed=true`、无未来 source。
7. **快照审计**：所有日期目录的 `stock_pool*.db` 同一成员表哈希；无第二个历史成员表可用
   （见 §2.1）。

## 6. 已知偏差与缺口（必须随结果一起读）

1. **稀疏，不是完整成员表**：`concept_hist` 每概念每天只给 1 只 leader。父主题日已知成员
   通常 1–18 只，而当前快照父主题有 500–1,700 只。不要把 `n_members` 当主题宽度。
2. **leader 选择偏差**：`buy_sm_amount_stock` 是“小单买入金额最大”，偏向散户/小单活跃票，
   不是市值/龙头/随机样本；它只证明“属于该概念”，不代表代表性。
3. **前值填充**：3,480 行 `ffill_repeat` 不是当天新观测；长 run 的 `quality=low`。
   严格 as-of 请过滤 `observation_type=fresh`，或查询时 `--max-staleness-days 0`。
4. **名称映射幸存者偏差**：股票名→代码用当前 `stock_pool` + 申万缓存；退市/改名/北交所
   代码不一致的 leader 会被丢弃。132 个名字、591 个单元格未映射（占主题匹配 leader 的 1.54%），
   清单在 `unmatched_leaders.csv`。未映射不猜代码。
5. **当期股票池幸存者偏差**：`stock_pool` 是当前 5,551 只，已退市股票不在其中；
   2025 年当时的退市票无法恢复。
6. **chain_map 选择偏差**：只收 segment 的 top candidates / verified / AI promoted，
   不是完整成分；`verified_all` 受构建时（可能是未来）的财务核实影响，但不会把非成员写成成员。
   仅 8 个日期，且集中在 2026-09-17 之后。
7. **主题分类是当前口径**：14 父主题 + 49 子主题来自当前 `THEME_CONCEPTS` / `SUBTEMES`；
   历史概念改名、拆分、合并没有重建。一个股票可同时属于多个主题（概念多标签）。
8. **别名含少量子集别名**：`CONCEPT_ALIASES` 里多数只是写法变体（空格、Ⅱ/Ⅲ、`AI 芯片`），
   也有少量子集别名（如 `游戏Ⅱ→网络游戏`、`中药Ⅱ→中药概念`、`银行` 仅 chain_map）。
   它们只会漏掉子集外的成员，不会把非成员写进来，但会低估覆盖；完整清单见 manifest
   的 `theme_mappings.aliases`。
9. **2025 年缺口**：`concept_hist` 从 2025-08-20 开始；2025-01-01~2025-08-19 没有任何 PIT
   成员证据。2025-08-20~2025-12-31 只有稀疏 leader 证据，不是完整成分。
10. **`net_amount` 不可靠**：生产实测 `concept_hist.net_amount` 末 5 日可能完全相同（前值填充）；
   本实现不用它，只用 `leader`。
11. **`bt_seed_day.py` 相关缺陷（未改，超出本任务范围）**：其 `truncate_concept_hist()`
    只截断 `dates/close/net_amount/vol/amount`，**没有截断 `leader`**。如果回测沙箱直接读
    `concept_hist.leader`，即便日期被截断，未来 leader 仍可能留在数组里。本构建器自己按
    `date` 逐日取 leader，不受该缺陷影响；但现有回测链路应单独修。

## 7. 复现 / 查询

```bash
# 构建（默认 strict：chain_map 只收 >= 2026-09-16）
.venv/bin/python jobs/build_pit_theme_members.py

# 严格 as-of 查询（默认 max-staleness-days=0，只用当天证据）
.venv/bin/python jobs/build_pit_theme_members.py --asof 20250820 --theme '半导体/芯片'

# 需要“成员延续”时显式放宽（会引入持续性假设）
.venv/bin/python jobs/build_pit_theme_members.py --asof 20260812 --theme 'AI/算力/科技' --max-staleness-days 5

# 测试
.venv/bin/python -m pytest tests/test_pit_theme_members.py -q
```

产物（`data/pit/theme_members/`，约 12MB）：

| 文件 | 行数 | 用途 |
|---|---:|---|
| `observations.csv` | 43,741 | 逐条日期证据，核心表 |
| `coverage.csv` | 16,254 | 主题 × 日期成员数（含 0，显式暴露缺口） |
| `theme_summary.csv` | 63 | 主题级首末日期、成员数分布、概念匹配 |
| `current_snapshot_not_pit.csv` | 19,492 | 当期快照，`pit=false`，禁止历史回测 |
| `unmatched_leaders.csv` | 132 | 未映射 leader 名字/次数/原因 |
| `manifest.json` | — | 输入哈希、口径、覆盖、缺口/偏差、校验 |
| `validation.json` | — | leak tests 明细 |

## 8. 阻塞与相关发现

1. **`jobs/eval_select_v0.py` 源文件缺失**：只有 `jobs/__pycache__/eval_select_v0.cpython-312.pyc`。
   `jobs/eval_select_v03.py` / `eval_select_v04.py` 仍 `from eval_select_v0 import ...`，
   当前无法运行。pyc 字符串显示它读 `.dsh-tmp/buyside/prod_data/stock_pool.db` 的
   `stock_concept_map` 建 `theme_map()` —— 正是本任务要修的“当期快照套历史”问题。
2. **PostgreSQL 不可达**：`127.0.0.1:18789` 连接超时；`data/_bt_pg` 目录 mode 700、
   owner 999，当前用户不可读。因此 PG `daily_artifacts` / `_bt_pg` 里的按日产物无法审计，
   本实现未使用。
3. **没有完整历史概念成员源**：本地没有 Eastmoney/Tushare 概念成分的逐日归档；
   `concept_hist` 只存 leader，不存完整成员。要补完整 2025 成员，需要新的外部归档源
   （或云上历史 PG 备份），不能靠现有文件外推。
4. **chain_map 生成链路仍读当期 DB**：`apps/main_line/chain_map.py` 用
   `sqlite3.connect(DATA/stock_pool.db)` 现查成员；未来若继续每天落 chain_map，
   应在生成时把 `stock_concept_map` 的 `as_of` 哈希/快照日期写进文件，否则日期文件仍不可
   直接当 PIT。

## 9. 验收结论

- ✅ 没有把当期 `stock_concept_map` 套到历史日期上；快照单独文件、`pit=false`。
- ✅ chain_map 只使用不早于本地成员表快照时间的 8 个日期。
- ✅ 2025 年缺口明确写在 manifest / 本文档，没有编造完整成员。
- ✅ 覆盖 14/14 父主题、32/49 子主题；零覆盖子主题逐个列出。
- ✅ 14 项 leak tests + 7 个 pytest 全过。
- ⚠️ 结果**稀疏且带 leader 选择偏差**；只能当“已知 PIT 成员”，不能当完整主题宽度/等权篮子。
