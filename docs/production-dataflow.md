# 生产数据流与回放可行性（2026-09-12）

> 目的：在做任何"按层级重跑"之前，先把**生产每天实际的数据获取与传递顺序**钉死，
> 逐环标出「输入 / 接口 / 计算 / 产物 / 消费方 / 能否逐日重放」。
> 来源：`config/tasks.yaml`（51 个任务）+ 各脚本头部说明 + 生产实测。

---

## 1. 一天的全景时序

```
【T−1 日 盘后】
18:00  chain_map / theme_gap_scan
18:40  wolf_limit_ladder_scan        （涨停梯队，EOD）
18:45  wolf_index_futures            （期指多空 → 次日黄白线预判）
18:45  mainline_gate_daily  ★主线链（6 步，见 §2）
18:50  wolf_theme_resilience         （跌得少弹得早；**已按用户决定关闭**）
19:40  wolf_review_score_run         （复盘打分表）
19:30  news_evening
15:01  daily_snapshot / 15:05 systemic_risk_monitor / 15:06 macro_state_collector
15:20  p2_gate_daily_report / 15:25 rotation_switch_dryrun + plan_generate_daily
16:00  daily_review / 16:30 index_daily_refresh（刷 000001.SH.csv）

【T 日 盘前 08:00–09:20】
08:00  main_line_judge
08:05  rotation_universe_refresh（derive_sub_universe）
08:10  wave_judge  ★波浪（LLM：上证结构+量能+锚点+主线 → level/sub_level/operation）
08:15  sector_g3_judge / 08:18 tranche_ladder_report
08:20  stock_confirm_refresh（选股确认池 → stock_confirm_result.json）
08:25  daily_strategy_summary
09:00  pre_market_scan / 09:10 morning_diagnosis（读 wave_state）/ 09:20 market_scan
09:20  rotation_switch_arm  ★布腿（写 t_conditions：253/254/低吸腿）
09:31  mainline_open_buy
09:50  rotation_switch_agent_morning ｜ 14:10 afternoon

【T 日 盘中】
TMonitor 30s 轮询 → 快照(quote/index/m5/vol_ratio/boll/support…) → 条件求值 → t_triggers(pending)
t_bridge / 决策层消费 pending → t_gateway.gateway_execute → paper_* 表
*/30  wolf_neg_event_scan（层①「无利空」）｜ 09:20/09:31/09:50/10-11/13-14  market_scan
auto_trade_*：09:35 / 09:53 / 10:35 / 13:35 / 14:30
```

---

## 2. 主线链（`mainline_gate_daily.py`，18:45）—— **6 步，权威顺序**

| # | 步骤 | 命令 | 输入 | 接口 | 产物 | 消费方 |
|---|---|---|---|---|---|---|
| 1 | concept_long | `build_concept_long.py 20250101` | 全市场日线 | gzcloud/tushare daily | `concept_long.json`（169 概念等权指数，meta.start=20250101；**增量**：end≥当日则 skip） | trend_confirm / heat_v2 / build_inst_flow |
| 2 | ETF 份额流 | `build_etf_flow.py --date` | 12 主题↔宽基 ETF 映射 + 两融 | tushare `fund_share`/`fund_daily`/`margin` | `etf_share_flow.json` | **heat_v2**（因子之一） |
| 3 | 机构通道 | `build_inst_flow.py --date` | 龙虎榜机构席位 + 北向持股 | tushare `top_inst`/`hk_hold` | `theme_inst_flow.json` | ⚠️ **无生产消费方**（详见 §4） |
| 4 | 结构 GATE | `trend_confirm.py --hist concept_long --params … --as-of d --json trend_confirm_<d>_long.json` | concept_long(≤d) + trend_gate_params + THEME_CONCEPTS | 无（纯计算） | `trend_confirm_<d>_long.json`（逐主题 A/B/GATE） | mainline_gate |
| 5 | 热度 | `heat_v2.py --date d` | concept_long(≤d) + stock_pool.db + etf_share_flow.json + concept_hist(**备用**) | tushare `moneyflow_dc` 逐日 20 天 | `heat_v2_<d>.json` | mainline_gate |
| 6 | 主线闸 | `mainline_gate.py --date d --fusion-json heat_v2_<d>.json` | trend_confirm_<d>_long + heat_v2_<d> + wave_state(**仅风险提示**) + concept_hist + main_line_state_<d>(catalyst) | 无 | `mainline_gate_<d>.json`（rows[].gate/verdict）+ `mainline_confirm_history.json` | **rotation_switch_arm（布腿）**、agent 上下文 |

---

## 3. 回放可行性矩阵（★=能否逐日重建）

| 输入/产物 | 形态 | 能否逐日重建 | PIT 情况 | 备注 |
|---|---|---|---|---|
| `concept_long.json` | **单文件累积**（20250101→当日） | ✅ 可直接用（脚本按 `--as-of`/`≤date8` 切） | ✅ PIT | trend_confirm/heat_v2/build_inst_flow 都按日期截断 |
| `etf_share_flow.json` | **单文件、当日覆盖** | ✅ `build_etf_flow --date d` | ✅ | **上一轮回放我用了静态文件 → 错+前视，必须重做** |
| `theme_inst_flow.json` | 单文件、当日覆盖 | ✅ `build_inst_flow --date d` | ✅ | 但**无消费方**（见 §4），重建它对保真度无帮助 |
| `trend_confirm_<d>_long.json` | 逐日文件 | ✅ 重放生成 | ✅ PIT | 已跑通 |
| `heat_v2_<d>.json` | 逐日文件 | ✅ 重放生成 | ✅ PIT（按 date8 切、逐日拉 moneyflow_dc） | 已跑通 |
| `mainline_gate_<d>.json` | 逐日文件 | ✅ 重放生成 | ⚠️ 含 `wave_env` 前视字段（docstring 明确**不参与资格判定**） | 保真度见 §5 |
| `main_line_state_<d>.json`（研报 catalyst） | 逐日文件 | ⚠️ **历史缺**（生产只留近期） | — | 回放走空 catalyst 兜底 → **保真度损失主因** |
| `concept_hist.json` | 单文件（frozen @Sep 8） | ❌ 无逐日版本 | ⚠️ | heat_v2 里仅"备用"因子；`build_inst_flow`/`position_class` 用它 |
| `stock_pool.db` | 静态（mysqlite） | ✅ 直接复制 | — | 概念↔成分映射 |
| `stock_confirm_result.json` | 单文件、当日覆盖 | ⚠️ 由 08:20 `stock_confirm_judge` 生成（含 LLM 判定？） | — | 布腿选股输入；回放需逐日重建或声明近似 |
| **`wave_state.json`**（大盘浪） | 单文件、**LLM 产物** | ✅ **`wave_agent.py --date=<d>` 可逐日重放**（内部取数均 `end_date=date` 截断 → PIT） | ✅（重放非当时存档） | 这是"波浪层"的可行数据源 |
| `t_regime_state`（环境闸门） | **DB 表** | ⚠️ 只有 **25 行**（20260815→20260911） | ✅ | 字段 `regime/gate_low_buy/gate_high_sell`；盘中由 TMonitor/gateway 写 |
| 指数日线 CSV `000001.SH.csv` | 单文件累积 | ✅ | ✅ | wave_agent 直接读 |

---

## 4. 顺带发现（做这张表时查出来的）

- **`theme_inst_flow.json` 无生产消费方**：`build_inst_flow.py` 每天跑（~15s）产出它，
  但全仓 grep 只有临时脚本读 → **"算了没人用"**（与之前那批"死守卫/死列"同属静默失效家族）。
  它**不影响 gate 保真度**，所以重建它对重跑没有价值（除非将来接入）。
- **`wave_state.json` 的路径是相对路径**（`STATE_FILE='data/wave_state.json'`，不读 `DATA_DIR`）→
  依赖进程 cwd；这与我们此前发现的 `DATA_DIR` 缺失坑同源。

---

## 5. 上一轮回放的已知偏差（本次重跑要修的）

| 偏差 | 影响 | 修法 |
|---|---|---|
| 只跑了 3 步（漏 `build_etf_flow`、`build_inst_flow`） | heat_v2 的 ETF 因子缺失 | **补齐 6 步**（inst_flow 可跑但不影响结果） |
| `etf_share_flow.json` 用静态（=今日）文件 | **前视 + 错误** | 逐日 `build_etf_flow --date d` |
| 缺 `main_line_state_<d>.json`（catalyst） | 保真度损失主因 | 无法补（历史缺）→ **在结论里显式声明** |
| `wave_state.json` 用当日快照 | `wave_env` 前视（不影响 gate 资格） | 逐日 `wave_agent --date d` 重放；**或**分层时用 `t_regime_state` |

---

## 6. 据此确定的"按层级重跑"方案

1. **第 1 层（主线）**：逐日跑完整 6 步链 → `mainline_gate_<d>.json`（74 天窗口）→ 取 `rows[].gate`
2. **第 2 层（波浪/环境）**：
   - 主口径：`wave_agent.py --date=<d>` 逐日重放 → `operation ∈ {build,t_only,side,defense,exit}`
   - 交叉校验：`t_regime_state.regime`（仅 25 天）
   - 声明：重放的波浪是**同一套 prompt+规则、按日期截断输入**生成的，**不等于当时存档**（LLM 有随机性）
3. **第 3 层（细节）**：A9（日级代理，待 ETF 分钟代理黄白线）/ A5 / A6 / A10
   —— **只在「主线确认 ∧ 波浪允许做T」的格子内评**；每格报 n，**n<100 标"无结论"**
4. **产出**：分层表（层 × 机制 × 指标）+ 对 §20 中 A9 结论的**显式更正**
