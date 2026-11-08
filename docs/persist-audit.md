# json.dump 落盘审计（2026-11-08）

扫描 `jobs/ scripts/ apps/ core/ backend/app`（排除 tests/fixtures）。
共 **470 处** `json.dump`，分布在 **372** 个文件。

> 口径差异：`scripts/lint_persist.py` 的棘轮只扫 `jobs/ backend/app/ apps` 记 **435** 处；
> 本审计额外包含 `scripts/` 与 `core/`，所以是 **470** 处。两个数字都对，只是范围不同。

复现：`.venv/bin/python scripts/audit_persist.py`（迁完一批重跑，能看到 A/B 数字下降）。

## 判定方法

1. 解析每个调用点的目标文件名（同语句 / 邻近行 / `with open(...)` 上下文）；
2. 统计该文件名**被仓库里多少个其它文件引用**——只有被多处读写的才是「共享契约」，才值得进 PG；
3. 拿不到文件名的，按上下文归到 CLI 指定 / 常量目录 / 未解析。

> ⚠️ 已知局限：按 basename 统计，同名不同目录会被合并（例如 `latest.json` 有 5 个写入方但分散在不同目录）。
> A/B 表里这类条目需要人工确认是否真的是同一个文件。

## 分类结果

| 分类 | 处数 | 处置 |
|---|---|---|
| A. 共享状态 → 迁 PG | 25 | 建表迁移（优先级最高） |
| B. 共享产物 → 集中管理 | 74 | 定一条契约规矩 |
| C. 临时文件混在 data/ | 0 | 挪 .dsh-tmp |
| F. 私有产物/缓存 | 145 | 保留 |
| G. CLI/调用方指定输出 | 42 | 保留 |
| H. 常量目录下输出 | 33 | 保留 |
| I. 未解析（变量路径） | 151 | 暂缓，lint 兜底 |

## A 类：真正的共享状态（17 个文件 / 25 处）

| 读取方文件数 | 写入处 | 文件名 | PG 对应表 | 写入方 |
|---|---|---|---|---|
| 43 | 2 | `wave_state.json` | wave_state（已双写） | `apps/main_line/wave_ladder.py` |
| 32 | 1 | `main_line_state.json` | 无 | `apps/main_line/mainline_state_inject.py` |
| 17 | 1 | `position_class_result.json` | 无 | `apps/main_line/position_class.py` |
| 6 | 2 | `latest_hot_sectors.json` | 无 | `apps/news/news_collector.py` |
| 6 | 1 | `wave_state_%s-%s-%s.json` | wave_state（已双写） | `apps/main_line/wave_ladder.py` |
| 6 | 1 | `macro_state.json` | 无 | `apps/main_line/build_macro_state.py` |
| 6 | 1 | `bellwether_state_latest.json` | 无 | `jobs/build_bellwether_state.py` |
| 2 | 3 | `rotation_switch_plan.json` | 无 | `jobs/rotation_switch_agent.py` |
| 2 | 1 | `theme_sleeve_positions.json` | 无 | `jobs/theme_sleeve_trade.py` |
| 2 | 1 | `sector_g3_state.json` | 无 | `jobs/sector_g3_judge.py` |
| 2 | 1 | `rotation_quadrant_history_pit.json` | 无 | `apps/main_line/backtest_rotation_quadrant_pit.py` |
| 2 | 1 | `rotation_quadrant_history.json` | 无 | `apps/main_line/backtest_rotation_quadrant.py` |
| 2 | 1 | `rotation_position_replay.json` | 无 | `apps/main_line/validate_rotation_cases.py` |
| 1 | 5 | `latest.json` | 无 | `backend/app/services/daily_decision.py` |
| 1 | 1 | `{a.date}_snapshot.json` | 无 | `scripts/asof_data.py` |
| 1 | 1 | `stock_agent_positions.json` | 无 | `jobs/stock_agent_position.py` |
| 1 | 1 | `_plan.json` | 无 | `jobs/bt_fetch_mins1.py` |

**排序靠前的三个就占了 92 个读取方**：`wave_state.json`(43) / `main_line_state.json`(32) /
`position_class_result.json`(17)。先动这三个，收益最大。

其中 `wave_state` 已经有 PG 表且在双写——迁移不是「建新表」，而是**摘掉文件分支**，是最小的一步。

> ✅ 2026-11-08 已落地：5 个读点切到 `core.wave`，`data/wave_state*.json` 与
> `data/wave_state_history/` **已删除**（备份见 NOW.md）。
> ✅ 2026-11-08 同步完成：`main_line_state` / `position_class` 两张表已建并回填（灰度 `both`）。

## B 类：共享产物（Top 20）

| 读取方 | 写入处 | 文件名 | 写入方 |
|---|---|---|---|
| 73 | 5 | `.json` | `apps/main_line/chain_dict_refine.py` |
| 18 | 5 | `%s.json` | `apps/main_line/theme_member_llm.py` |
| 16 | 1 | `stock_confirm_result.json` | `apps/main_line/stock_confirm_judge.py` |
| 12 | 1 | `concept_long.json` | `apps/main_line/build_concept_long.py` |
| 9 | 1 | `etf_theme_map_pi.json` | `apps/main_line/build_etf_map_pi.py` |
| 7 | 1 | `rotation_universe_result.json` | `apps/main_line/rotation_universe.py` |
| 7 | 1 | `rev_map.json` | `jobs/bt_code_revs.py` |
| 7 | 1 | `index5_sina.json` | `apps/main_line/_recent_sync_pull.py` |
| 6 | 5 | `theme_sleeve_facts.json` | `jobs/apply_agent_leader_to_facts.py` |
| 6 | 1 | `wolf_labels_v2.json` | `apps/main_line/merge_labels.py` |
| 6 | 1 | `trend_confirm_params.json` | `apps/main_line/trend_confirm_calibrate.py` |
| 5 | 2 | `index_daily_%s.json` | `jobs/fetch_index_daily_em.py` |
| 5 | 1 | `rotation_crowding.json` | `apps/main_line/build_crowding.py` |
| 4 | 3 | `theme_sleeve_selected.json` | `jobs/theme_ab_apply.py` |
| 4 | 1 | `stock_crowd_%s.json` | `apps/main_line/build_fund_pit.py` |
| 4 | 1 | `macro_calendar.json` | `jobs/sync_macro_calendar.py` |
| 4 | 1 | `etf_share_flow.json` | `apps/main_line/build_etf_flow.py` |
| 3 | 1 | `theme_candidate_leader_agent.json` | `jobs/theme_candidate_leader_agent.py` |
| 3 | 1 | `systemic_risk.json` | `apps/main_line/systemic_risk.py` |
| 3 | 1 | `stock_agent_selected.json` | `scripts/stock_agent_review.py` |

## I 类：未解析（151 处）

`json.dump(x, f)` 且 `f` 来自函数参数/复杂表达式，本次无法自动判定。不影响正确性，
优先级最低；`scripts/lint_persist.py` 的棘轮保证这个数字只降不升。

| 文件 | 行 | 源码 |
|---|---|---|
| `jobs/regen_wave_state_ladder.py` | 261 | `json.dump(ws, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)` |
| `jobs/build_pit_chain_map.py` | 135 | `json.dump(obj, f, ensure_ascii=False, indent=1)` |
| `jobs/build_pit_chain_map.py` | 153 | `json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))` |
| `jobs/bt_seed_day.py` | 129 | `json.dump(obj, f, ensure_ascii=False)` |
| `jobs/rotation_switch_arm.py` | 236 | `json.dump(rec, f, ensure_ascii=False, indent=1)` |
| `jobs/rotation_switch_arm.py` | 670 | `json.dump(cur, f, ensure_ascii=False, indent=1)` |
| `jobs/rotation_switch_arm.py` | 1340 | `json.dump(_hp, _f, ensure_ascii=False, indent=1)` |
| `jobs/core_bellwether_daily.py` | 35 | `json.dump({"date": x.get("date"), "source": "unified_all_subthemes", "items": it` |
| `jobs/core_bellwether_daily.py` | 76 | `json.dump(out, open(PIPELINE, "w", encoding="utf-8"), ensure_ascii=False, indent` |
| `jobs/core_bellwether_daily.py` | 53 | `json.dump(out, open(PIPELINE, "w", encoding="utf-8"), ensure_ascii=False, indent` |

## 推进顺序（建议）

1. **wave_state 摘文件分支**（已有表+双写）：改完这一个，43 个读取方就不用再碰文件。
2. **main_line_state / position_class_result 建表**：读取方 32 / 17，是第二梯队。
3. **其余 A 类逐个建表**：单个影响面小，可穿插做。
4. **B 类定规矩**：要么进 PG，要么在 `docs/file-contracts.md` 登记「谁写/谁读/schema」。
5. **I 类暂缓**：等前面清完再回头看。

每完成一批，跑 `.venv/bin/python scripts/lint_persist.py --update-baseline` 把基线压下去。
