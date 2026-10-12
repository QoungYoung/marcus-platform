# 自设规则盘点（AST 精准版）

## 一、人工挑选：影响最大的自设规则（建议优先处理）

| 权重 | 类别 | 位置 | 判据 | 出处判断 |
|---|---|---|---|---|
| 3 | 风控·仓位 | `backend/app/api/indicator.py:1652` | 震荡市 ⇒ `single_cap ≤ 8.0`、`total_cap ≤ 50.0`、green→yellow ✗ | **自设**：狼大原话是 主升75+/调整50/有风险30；他说「震荡筑底」时是 **75%**、「缩量微震」是 **60-70%** |
| 3 | 风控·仓位 | `backend/app/api/indicator.py:1192` | 风格倍率 `OFFENSE×1.15 / DEFENSE×0.65（总仓×0.75）/ RESOURCE_HEDGE×0.85` ✗ | **自设**（无任何引用）|
| 3 | 风控·仓位 | `backend/app/api/indicator.py:2929` | `max_position_pct = 5.0` ✗ | **自设**：台账原话「只给总量档位…**没给单笔**」|
| 3 | 风控·仓位 | `backend/app/api/market.py:1874/1909/1941/1954/1959` | 震荡/趋势投票阈值（振幅>25%/<15%、连续≥5天、涨跌停比>5或<0.2、MA5角>30°）✗ | **自设阈值**：他讲的是「情形」（放量突破？急杀？位置？）|
| 3 | 风控·仓位 | `backend/app/services/position_tier_monitor.py:374/384/394/2001` | 档位升级阈 `pnl_pct ≥ 0.03 / 0.01` ✗ | 待人工核（未见引用）|
| 3 | 选股·额度 | `backend/app/services/t_build.py:1498` | `DEFAULT_AUTO_SELECT_LIMIT = 5`（每日自动选股上限）✗ | 待人工核 |
| 3 | 判据·门槛 | `backend/app/services/t_build.py:516` | 涨停占比 `> 0.1` 才放行 ✗ | 待人工核 |
| 3 | 风控·仓位 | `backend/app/services/golden_pit_config.py:337` | `PRE_TURN_CUMULATIVE_CAP = 0.15` ✗ | 待人工核 |
| 2 | 分类·风格 | `backend/app/services/industry_leaderboard.py`（多处）| `ADX>25` / MACD金叉 加分、`cap_score=4` 等 ✗ | **自设**（源自设计文档提案；仅打分，不入风控 ✓）|
| 1 | 噪音核对 | `t_eod.py:22 STOP_LOSS_PCT=0.03` | -3% 止损 ✗ | **有出处 ✓**（狼大 3-05「无利空、13日内下跌创新低后 -3%」）⇒ 属误报，保留 |

## 二、为什么这些自设规则会漏掉（关键 ✗）

项目本来有**出处纪律** ✓：`docs/wolf-buy-parameter-ledger.md` §4 记了 **11 项自设参数** ✓，
代码里要求逐条标 `⛔自设(无语料依据)` ✓，并有守卫测试 `backend/tests/test_param_provenance_marks.py` ✓。

**但守卫只覆盖 4 个文件** ✗：`wolf_confirm_pick.py`(9) / `rotation_switch_arm.py`(3) / `t_gateway.py`(5) / `wolf_ticket_ban.py`(1)。

⇒ `indicator.py` / `market.py` / `position_tier_monitor.py` / `t_build.py` **不在守卫内** ✗
⇒ 这些文件里新加的自设阈值（震荡市 8%/50%、风格倍率 ×1.15/×0.65、单笔 5%、档位 0.03/0.01 …）**没人强制申报出处** ✗
⇒ 所以它们既不在 §4 清单、也没有 `⛔自设` 标记 ⇒ **你只能靠逐个发现** ✗（本表就是补这个洞 ✓）。

建议（低成本、可执行 ✓）：把守卫的**文件清单扩到全部决策模块** ✓（indicator/market/t_monitor/t_build/position_tier_monitor/stop_loss_monitor/risk_gate…）
⇒ 以后任何自设阈值不标 `⛔自设` 就**测试失败** ✓，不用再靠人肉盘点 ✓。

## 三、机器扫描全量（本表共 620 条，按影响面排序；`邻行有引用` = ±8 行内出现语料/账本引用）

规则：只收运行时表达式里的 `参数赋值 / 阈值比较 / 倍率`，且**所在函数 ±40 行无语料引用**（`语料|狼大|账本§|§9.|原话|逐字|docs/wolf-|NGA|tid=`）

权重：3=风控/仓位相关 ｜ 2=分类/风格 ｜ 1=其他参数

| 权重 | 类别 | 位置 | 判据 | 原行 | 出处核验 |
|---|---|---|---|---|---|
| 3 | C 倍率 | `apps/main_line/backtest_intraday_t.py:41` | `vol_before * 0.8` | `stop_vol = vol_after < vol_before * 0.8 if vol_before > 0 else False` | **无引用 ✗** |
| 3 | C 倍率 | `apps/main_line/backtest_recent_two_weeks.py:81` | `vol_before * 0.8` | `stopped = vol_before>0 and vol_after<vol_before*0.8` | **无引用 ✗** |
| 3 | D 调用参数 | `apps/main_line/theme_leader_score.py:128` | `max(50 int ⇒ 50)` | `_cap = max(50, int(_tot * 0.20))` | **无引用 ✗** |
| 3 | D 调用参数 | `apps/main_line/trend_channel.py:829` | `_f(WOLF_TREND_MAX_LEGS 8 ⇒ 8)` | `_cap = int(_f("WOLF_TREND_MAX_LEGS", 8))` | 邻行有引用 |
| 3 | A 参数赋值 | `apps/main_line/wolf_confirm_pick.py:48` | `LIMITUP_PCT = 9.7` | `LIMITUP_PCT = 9.7         # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)` | 邻行有引用 |
| 3 | D 调用参数 | `backend/app/api/backtest.py:376` | `limit(200 ⇒ 200)` | `db.query(BacktestDailyLog)` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/api/backtest.py:399` | `limit(50 ⇒ 50)` | `db.query(BacktestPosition)` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/api/backtest.py:2873` | `max(limit 11 ⇒ 11)` | `while current >= start_dt and len(raw_records) < max(limit, 11):` | **无引用 ✗** |
| 3 | B 阈值比较 | `backend/app/api/backtest.py:4811` | `total_pos_pct'Add'0.18 Gt 0.2` | `if total_pos_pct + 0.18 > 0.20:  # rough check` | **无引用 ✗** |
| 3 | C 倍率 | `backend/app/api/indicator.py:375` | `total_days * 5` | `return max(0, int(total_days * 5 / 7))` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/api/indicator.py:1088` | `max(0.05  ⇒ 0.05)` | `stop_distance = entry_price * max(0.05, (atr / entry_price) * 1.5 if entry_price > 0 else 0.05)` | **无引用 ✗** |
| 3 | A 参数赋值 | `backend/app/api/indicator.py:1093` | `total_trading_minutes = 240.0` | `total_trading_minutes = 240.0  # 9:30-11:30 (120) + 13:00-15:00 (120)` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/api/indicator.py:1655` | `min(single_cap_pct 8.0 ⇒ 8.0)` | `single_cap_pct = min(single_cap_pct, 8.0)` | 邻行有引用 |
| 3 | D 调用参数 | `backend/app/api/indicator.py:1656` | `min(total_cap_pct 50.0 ⇒ 50.0)` | `total_cap_pct = min(total_cap_pct, 50.0)` | 邻行有引用 |
| 3 | B 阈值比较 | `backend/app/api/indicator.py:2642` | `capital_efficiency Lt 5` | `if capital_efficiency < 5:` | **无引用 ✗** |
| 3 | A 参数赋值 | `backend/app/api/indicator.py:2929` | `max_position_pct = 5.0` | `max_position_pct = 5.0` | **无引用 ✗** |
| 3 | B 阈值比较 | `backend/app/api/market.py:1895` | `limit_up'Div'limit_down Lt 0.2` | `if limit_down > 0 and limit_up / limit_down < 0.2:  # <1:5` | **无引用 ✗** |
| 3 | B 阈值比较 | `backend/app/api/market.py:1898` | `limit_ratio Gt 5` | `elif limit_ratio > 5:` | **无引用 ✗** |
| 3 | B 阈值比较 | `backend/app/api/market.py:1901` | `limit_ratio Lt 0.2` | `elif limit_ratio < 0.2:` | **无引用 ✗** |
| 3 | B 阈值比较 | `backend/app/api/market.py:1954` | `limit_ratio Gt 5` | `"signal": "趋势" if limit_ratio > 5 or limit_ratio < 0.2 else "震荡",` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/api/strategy.py:57` | `get(position_limit 80 ⇒ 80)` | `position_limit=state.get("position_limit", 80),` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/api/strategy.py:60` | `get(trailing_stop 5 ⇒ 5)` | `trailing_stop=state.get("trailing_stop", 5),` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/api/strategy.py:96` | `get(position_limit 80 ⇒ 80)` | `position_limit=data.get("position_limit", 80),` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/api/t_account.py:1056` | `HTTPException(400 positions 为空 ⇒ 400)` | `raise HTTPException(status_code=400, detail='positions 为空')` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/api/t_backtest.py:64` | `get(build_limit_ratio 0.55 ⇒ 0.55)` | `build_limit_ratio=float(body.get("build_limit_ratio", 0.55)),` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/backtest_monitors.py:565` | `get(initial_capital 100000 ⇒ 100000)` | `initial = acc.get('initial_capital', 100000)` | **无引用 ✗** |
| 3 | A 参数赋值 | `backend/app/services/golden_pit_config.py:337` | `PRE_TURN_CUMULATIVE_CAP = 0.15` | `PRE_TURN_CUMULATIVE_CAP = 0.15  # 拐点前累计上限` | **无引用 ✗** |
| 3 | A 参数赋值 | `backend/app/services/golden_pit_dca_service.py:59` | `PRE_TURN_CUMULATIVE_CAP = 0.15` | `PRE_TURN_CUMULATIVE_CAP = 0.15   # 拐点前累计上限 (占 max_total 比例)` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/golden_pit_dca_service.py:213` | `limit(4 ⇒ 4)` | `db.query(GoldenPitSnapshot)` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/golden_pit_dca_service.py:471` | `_fetch_tushare_kline(etf_code 40 ⇒ 40)` | `bars = GoldenPitService._fetch_tushare_kline(etf_code, limit=40)` | 邻行有引用 |
| 3 | D 调用参数 | `backend/app/services/golden_pit_dca_service.py:1967` | `get(initial_capital 250000.0 ⇒ 250000.0)` | `nav = acct.get("cash", acct.get("initial_capital", 250000.0))` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/golden_pit_industry_service.py:530` | `get(initial_capital 250000.0 ⇒ 250000.0)` | `nav = acct.get("cash", acct.get("initial_capital", 250000.0))` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/golden_pit_repository.py:181` | `_cached_tushare_kline(etf_code 500 ⇒ 500)` | `bars = service._cached_tushare_kline(etf_code, limit=500)` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/golden_pit_sector_service.py:698` | `get(entry_greed_cap 0.95 ⇒ 0.95)` | `greed_cap = float(params.get("entry_greed_cap") or cfg.get("entry_greed_cap", 0.95))  # 入场贪婪分位上限（过热不追）` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/golden_pit_service.py:809` | `_fetch_tushare_kline(etf_code 250 ⇒ 250)` | `bars = self._fetch_tushare_kline(etf_code, limit=250)` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/golden_pit_service.py:891` | `_cached_tushare_kline(etf_code 250 ⇒ 250)` | `bars = self._cached_tushare_kline(etf_code, limit=250) if etf_code else []` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/golden_pit_tech_status.py:91` | `_cached_tushare_kline(etf_code limit 7200 ⇒ 7200)` | `bars = get_golden_pit_service()._cached_tushare_kline(etf_code, limit=limit, ttl=7200)` | **无引用 ✗** |
| 3 | A 参数赋值 | `backend/app/services/industry_leaderboard.py:2141` | `cap_score = 4` | `cap_score = 4` | **无引用 ✗** |
| 3 | B 阈值比较 | `backend/app/services/position_tier_monitor.py:374` | `pnl_pct GtE 0.03` | `if current_tier in ('probe', 'unknown', '') and pnl_pct >= 0.03:` | **无引用 ✗** |
| 3 | B 阈值比较 | `backend/app/services/position_tier_monitor.py:384` | `pnl_pct GtE 0.01` | `if current_tier in ('probe', 'unknown', '') and pnl_pct >= 0.01:` | **无引用 ✗** |
| 3 | B 阈值比较 | `backend/app/services/position_tier_monitor.py:394` | `pnl_pct GtE 0.03` | `if current_tier == 'confirm' and pnl_pct >= 0.03:` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/position_tier_monitor.py:528` | `get(initial_capital 100000 ⇒ 100000)` | `initial = account.get('initial_capital', 100000)` | **无引用 ✗** |
| 3 | A 参数赋值 | `backend/app/services/position_tier_monitor.py:1323` | `limit = 20.0` | `limit = 20.0` | **无引用 ✗** |
| 3 | A 参数赋值 | `backend/app/services/position_tier_monitor.py:1325` | `limit = 20.0` | `limit = 20.0` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/position_tier_monitor.py:1703` | `get(current_tier 0.1 ⇒ 0.1)` | `tier_cap = self.TIER_CAPS.get(current_tier, 0.10)` | **无引用 ✗** |
| 3 | B 阈值比较 | `backend/app/services/position_tier_monitor.py:2001` | `float_pnl_pct GtE 0.01` | `if float_pnl_pct >= 0.01 and current_tier == 'probe':` | **无引用 ✗** |
| 3 | B 阈值比较 | `backend/app/services/scheduler_service.py:1123` | `prev_limit LtE 20` | `if prev_stance == 'red' or prev_limit <= 20:` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/t_backtest.py:468` | `get(max_daily_condition_ 8 ⇒ 8)` | `self._max_daily_rebuilds: int = int(task.get("max_daily_condition_rebuilds", 8))` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/t_backtest.py:1477` | `get(build_limit_ratio 0.5 ⇒ 0.5)` | `self.build_limit_ratio = float(task.get("build_limit_ratio", 0.50))` | 邻行有引用 |
| 3 | D 调用参数 | `backend/app/services/t_backtest.py:1716` | `get(cand_score_min 0.78 ⇒ 0.78)` | `_rot_min_score = float(_sp.get("cand_score_min", float(t_build._params().get("cand_score_min", 0.78))) or 0.78` | **无引用 ✗** |
| 3 | B 阈值比较 | `backend/app/services/t_build.py:516` | `limit_count'Div'max Gt 0.1` | `if limit_count / max(len(bars), 1) > 0.1:` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/t_build.py:860` | `get(tier 0.05 ⇒ 0.05)` | `single_pct = p["single_order_pct"].get(tier, 0.05)` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/t_build.py:861` | `get(tier 0.15 ⇒ 0.15)` | `per_symbol_pct = p["per_symbol_cap"].get(tier, 0.15)` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/t_build.py:862` | `get(tier 0.55 ⇒ 0.55)` | `total_pct = p["total_floor_cap"].get(tier, 0.55)` | **无引用 ✗** |
| 3 | A 参数赋值 | `backend/app/services/t_build.py:1498` | `DEFAULT_AUTO_SELECT_LIMIT = 5` | `DEFAULT_AUTO_SELECT_LIMIT = 5      # 每日自动选股数量上限` | **无引用 ✗** |
| 3 | A 参数赋值 | `backend/app/services/t_capacity.py:45` | `BASE_FLOOR_PCT = 65.0` | `BASE_FLOOR_PCT = 65.0        # 语料：仓位不会低于 65% 收盘（底仓下限目标）` | 邻行有引用 |
| 3 | A 参数赋值 | `backend/app/services/t_capacity.py:46` | `T_SLEEVE_PCT = 20.0` | `T_SLEEVE_PCT = 20.0          # 语料：日内做T仓位 20%` | 邻行有引用 |
| 3 | A 参数赋值 | `backend/app/services/t_capacity.py:61` | `MIN_BUILD_NOTIONAL = 8000.0` | `MIN_BUILD_NOTIONAL = 8000.0  # 单笔建仓最小有效规模（治"200 股建仓"；回测实测中位 6983 元）` | **无引用 ✗** |
| 3 | A 参数赋值 | `backend/app/services/t_eod.py:22` | `STOP_LOSS_PCT = 0.03` | `STOP_LOSS_PCT = 0.03        # 止损：买入后破位 -3% 卖出旧底仓对冲` | **无引用 ✗** |
| 3 | A 参数赋值 | `backend/app/services/t_gateway.py:351` | `MAX_SINGLE_ORDER_PCT = 0.05` | `MAX_SINGLE_ORDER_PCT = 0.05        # 单笔 ≤ 净值 5%（建议层）  # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)` | 邻行有引用 |
| 3 | A 参数赋值 | `backend/app/services/t_gateway.py:357` | `COST_RATIO_LIMIT = 0.2` | `COST_RATIO_LIMIT = 0.2             # 滑点+手续费 > 价差空间 20% 不触发` | 邻行有引用 |
| 3 | D 调用参数 | `backend/app/services/t_monitor.py:564` | `max(5.0 wait ⇒ 5.0)` | `self._stop.wait(max(5.0, wait))` | **无引用 ✗** |
| 3 | A 参数赋值 | `backend/app/services/t_monitor.py:1147` | `_cap = 5` | `_cap = 5` | **无引用 ✗** |
| 3 | A 参数赋值 | `backend/app/services/t_pool.py:79` | `STOP_LOSS_AMP_FACTOR = 0.55` | `STOP_LOSS_AMP_FACTOR = 0.55   # 止损 = 振幅中位 × 0.55（迭代 #36 后 0.40→0.55）` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/t_stop_loss_guard.py:77` | `get(stop_support_proximi 1.5 ⇒ 1.5)` | `proximity = float(params.get("stop_support_proximity_pct", 1.5))` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/t_stop_loss_guard.py:103` | `get(stop_confirm_bars 5 ⇒ 5)` | `n = int(params.get("stop_confirm_bars", 5) or 0)` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/trade_graph.py:284` | `max(100000 total_asset_market ⇒ 100000)` | `peak_equity = load_peak_equity(fallback=max(100000, total_asset_market), account_id='stock')` | **无引用 ✗** |
| 3 | B 阈值比较 | `backend/app/services/trade_graph.py:652` | `position_limit GtE 20` | `if position_limit > 0 and actual_pct < position_limit * 0.3 and position_limit >= 20:` | **无引用 ✗** |
| 3 | B 阈值比较 | `backend/app/services/trade_graph.py:1609` | `position_limit Gt 50` | `if position_limit > 50:` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/wolf_discipline.py:310` | `get(reduce_ratio 0.5 ⇒ 0.5)` | `ratio = float(pt.get("reduce_ratio", 0.5))` | **无引用 ✗** |
| 3 | A 参数赋值 | `backend/app/services/wolf_volume_gate.py:77` | `_TOTAL_MIN = 240.0` | `_TOTAL_MIN = 240.0` | **无引用 ✗** |
| 3 | D 调用参数 | `backend/app/services/wolf_weekend_hedge.py:198` | `get(reduce_ratio 0.5 ⇒ 0.5)` | `reduce_to = c.get("reduce_ratio", 0.5)` | **无引用 ✗** |
| 3 | A 参数赋值 | `jobs/_tmp_corpus_qty_gap.py:29` | `cap_per = 16` | `cap_per=16; cnt=collections.Counter()` | **无引用 ✗** |
| 3 | A 参数赋值 | `jobs/_tmp_corpus_select_rules.py:34` | `cap_per = 18` | `cap_per=18` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/_tmp_tier_monitor_smoke.py:339` | `PositionTierMonitor(ex 33 ⇒ 33)` | `mon = ptm.PositionTierMonitor(executor=ex, interval_seconds=33)` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/audit_container_paths.py:130` | `add_argument(--limit int 200 ⇒ 200)` | `ap.add_argument("--limit", type=int, default=200)` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/bt_account.py:215` | `add_argument(--order-budget float 25000.0 单笔预算（元，整手向下 ⇒ 25000.0)` | `ap.add_argument("--order-budget", type=float, default=25000.0, help="单笔预算（元，整手向下取整）")` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/bt_account.py:332` | `max(_px 0.01 ⇒ 0.01)` | `_want = _tqty if _tqty > 0 else int(min(a.order_budget, acct.cash * 0.98) / max(_px, 0.01) / 100) * 100` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/bt_account.py:396` | `max(px 0.01 ⇒ 0.01)` | `vol = int(budget / max(px, 0.01) / 100) * 100` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/bt_asof_api.py:1067` | `min(5000 int ⇒ 5000)` | `limit = max(1, min(5000, int(q.get("limit") or 500)))` | **无引用 ✗** |
| 3 | A 参数赋值 | `jobs/bt_asof_api.py:1069` | `limit = 500` | `limit = 500` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/bt_asof_api.py:1198` | `min(500 int ⇒ 500)` | `limit = max(1, min(500, int(q.get("limit") or 10)))` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/bt_report.py:157` | `max(b 0.01 ⇒ 0.01)` | `vol = int(min(budget, acct.cash * 0.98) / max(b["price"], 0.01) / 100) * 100` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/bt_report.py:288` | `add_argument(--budget float 25000.0 粗估：单笔预算（生产试仓档 ≈ 2 ⇒ 25000.0)` | `ap.add_argument("--budget", type=float, default=25000.0, help="粗估：单笔预算（生产试仓档 ≈ 2.5 万/笔）")` | **无引用 ✗** |
| 3 | A 参数赋值 | `jobs/eval_pick_selection.py:56` | `MAX_LEGS = 4` | `MAX_LEGS = 4          # tier1+tier2 上限 WOLF_PICK_MAX_LEGS` | **无引用 ✗** |
| 3 | A 参数赋值 | `jobs/eval_pick_selection.py:58` | `TIER2_GAP = 8.0` | `TIER2_GAP = 8.0       # WOLF_PICK_TIER2_GAP` | 邻行有引用 |
| 3 | A 参数赋值 | `jobs/eval_pick_selection.py:66` | `LIMITUP = 9.7` | `LIMITUP = 9.7         # 涨停判定阈值（%）` | 邻行有引用 |
| 3 | A 参数赋值 | `jobs/eval_stop_layers.py:55` | `EARLY_STOP_PCT = 0.03` | `EARLY_STOP_PCT = 0.03    # 波段低点 −3%` | 邻行有引用 |
| 3 | D 调用参数 | `jobs/extract_xlsx_themes.py:94` | `add_argument(--limit int 25 ⇒ 25)` | `ap.add_argument("--limit", type=int, default=25)` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/main_wave_analyzer.py:104` | `min(8 limit ⇒ 8)` | `top_n=args.limit, deep_n=min(8, args.limit),` | **无引用 ✗** |
| 3 | B 阈值比较 | `jobs/market_scan.py:298` | `vol_ratio Lt 0.5` | `risk_level = 'high' if pct_chg > 3 and vol_ratio < 0.5 else 'medium'` | **无引用 ✗** |
| 3 | A 参数赋值 | `jobs/market_scan.py:1141` | `initial_capital = 100000` | `initial_capital = 100000` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/market_scan.py:1260` | `min(position_limit 20 ⇒ 20)` | `position_limit = min(position_limit, 20)` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/market_scan.py:1268` | `min(position_limit 40 ⇒ 40)` | `position_limit = min(position_limit, 40)` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/market_scan.py:1274` | `min(position_limit 15 ⇒ 15)` | `position_limit = min(position_limit, 15)` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/market_scan.py:1292` | `min(position_limit 50 ⇒ 50)` | `position_limit = min(position_limit, 50)` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/market_scan.py:1296` | `min(position_limit 40 ⇒ 40)` | `position_limit = min(position_limit, 40)` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/market_scan.py:1325` | `min(position_limit 20 ⇒ 20)` | `position_limit = min(position_limit, 20)` | **无引用 ✗** |
| 3 | B 阈值比较 | `jobs/market_scan.py:1421` | `position_limit GtE 40` | `elif position_limit >= 40:` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/market_scan.py:1438` | `min(l1_hard_cap 20 ⇒ 20)` | `l1_hard_cap = min(l1_hard_cap, 20)  # 缺口已覆盖，底线从15放宽到20` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/market_scan.py:1441` | `min(l1_hard_cap 15 ⇒ 15)` | `l1_hard_cap = min(l1_hard_cap, 15)  # 独立触发，底线15%` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/market_scan.py:1447` | `min(l1_hard_cap 20 ⇒ 20)` | `l1_hard_cap = min(l1_hard_cap, 20)` | **无引用 ✗** |
| 3 | B 阈值比较 | `jobs/market_scan.py:1483` | `position_limit GtE 40` | `elif position_limit >= 40:` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/mine_selfset_params.py:50` | `add_argument(--limit int 6 每个参数打印多少条带数值的命中 ⇒ 6)` | `ap.add_argument("--limit", type=int, default=6, help="每个参数打印多少条带数值的命中")` | **无引用 ✗** |
| 3 | B 阈值比较 | `jobs/pre_market_scan.py:388` | `neg'Div'max Gt 0.4` | `has_negative_risk = (neg / max(total, 1) > 0.4) or (neg >= 3)` | **无引用 ✗** |
| 3 | B 阈值比较 | `jobs/pre_market_scan.py:455` | `pos_ratio GtE 0.4` | `elif trend == 'rising' and total >= 3 and pos_ratio >= 0.4:` | **无引用 ✗** |
| 3 | B 阈值比较 | `jobs/pre_market_scan.py:461` | `pos_ratio GtE 0.5` | `elif pos_ratio >= 0.5 and total >= 2:` | **无引用 ✗** |
| 3 | D 调用参数 | `jobs/scan_xlsx_gaps.py:52` | `add_argument(--limit int 40 最多打印多少条增量判据 ⇒ 40)` | `ap.add_argument("--limit", type=int, default=40, help="最多打印多少条增量判据")` | **无引用 ✗** |
| 2 | B 阈值比较 | `backend/app/api/market.py:1941` | `max_consecutive GtE 5` | `"signal": "趋势" if max_consecutive >= 5 else "震荡",` | **无引用 ✗** |
| 2 | D 调用参数 | `backend/app/services/golden_pit_sector_service.py:850` | `get(regime_trend_thresho 5 ⇒ 5)` | `threshold = int(cfg.get("regime_trend_threshold", 5))` | **无引用 ✗** |
| 2 | B 阈值比较 | `jobs/daily_review_enhanced.py:416` | `scores Gt 40` | `- 指数表现: {scores['index_score']:.0f}/100（{'趋势向上' if scores['index_score'] > 60 else '震荡' if scores['index_score` | **无引用 ✗** |
| 2 | B 阈值比较 | `jobs/daily_review_enhanced.py:417` | `scores Gt 40` | `- 新闻情绪: {scores['sentiment_score']:.0f}/100（{'偏暖' if scores['sentiment_score'] > 60 else '中性' if scores['senti` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/backtest_confirm_calibrate.py:87` | `min * 0.3` | `score = hitA + 0.3 * min(meanA / 5.0, 1.0) - 0.2 * (len(A) / len(samples))` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/backtest_intraday_t5.py:37` | `vol_before * 0.8` | `if not (vol_before > 0 and vol_after < vol_before * 0.8): return False, "高点后未停量"` | **无引用 ✗** |
| 1 | D 调用参数 | `apps/main_line/backtest_min15.py:32` | `min15_stand(win 20 3 ⇒ 20)` | `stand = min15_stand(win, look=20, stand_n=3) if len(win) >= 23 else False` | **无引用 ✗** |
| 1 | D 调用参数 | `apps/main_line/backtest_recent_two_weeks.py:119` | `find_254(dm days di 1.2 ⇒ 1.2)` | `rr=find_254(dm,days,di,thresh_vr=1.2)` | **无引用 ✗** |
| 1 | D 调用参数 | `apps/main_line/backtest_wolf_253_stepwise.py:193` | `find_254_on(dm days i 1.2 ⇒ 1.2)` | `rr = find_254_on(dm, days, i, thresh_vr=1.2)` | **无引用 ✗** |
| 1 | D 调用参数 | `apps/main_line/backtest_wolf_dip_254.py:78` | `min(20 i ⇒ 20)` | `avg=sum(r['v'] for r in rows[max(0,i-21):i])/max(1,min(20,i))` | **无引用 ✗** |
| 1 | D 调用参数 | `apps/main_line/backtest_wolf_extra_events.py:86` | `find_254_on(dm days i 1.2 ⇒ 1.2)` | `rr = S.find_254_on(dm, days, i, thresh_vr=1.2)` | **无引用 ✗** |
| 1 | B 阈值比较 | `apps/main_line/bt_noise_effect.py:51` | `share Lt 0.65` | `return bool(len([s for s in subs if nets[s] > 0]) >= 2 and share < 0.65)` | **无引用 ✗** |
| 1 | B 阈值比较 | `apps/main_line/bt_noise_effect.py:65` | `share Lt 0.8` | `return bool(len(sig_in) >= 2 and share < 0.8)` | **无引用 ✗** |
| 1 | A 参数赋值 | `apps/main_line/bt_sub_universe_dsh.py:36` | `MAX_SUBS = 9` | `MAX_SUBS = 9` | **无引用 ✗** |
| 1 | B 阈值比较 | `apps/main_line/bt_sub_universe_dsh.py:210` | `top1_share Lt 0.65` | `healthy = bool(len(leader_in) >= 2 and top1_share < 0.65)` | **无引用 ✗** |
| 1 | B 阈值比较 | `apps/main_line/bt_sub_universe_dsh.py:211` | `top1_share GtE 0.65` | `sucking = bool(top1_share >= 0.65 or len(leader_in) <= 1)` | **无引用 ✗** |
| 1 | A 参数赋值 | `apps/main_line/build_concept_taxonomy.py:17` | `MAX_SUBS_PER_THEME = 9` | `MAX_SUBS_PER_THEME = 9   # 用户指定: 按写死科技类子方向数(国算/液冷/存储/材料/芯片/光通信/AI应用/AI终端/铜缆电源)` | **无引用 ✗** |
| 1 | D 调用参数 | `apps/main_line/build_concept_vol.py:61` | `min(120 n ⇒ 120)` | `w=min(120, n)` | **无引用 ✗** |
| 1 | A 参数赋值 | `apps/main_line/chain_kw_autofix.py:18` | `MIN_CONF = 0.9` | `MIN_CONF = 0.9` | **无引用 ✗** |
| 1 | A 参数赋值 | `apps/main_line/chain_map.py:1287` | `AI_CONF_MIN = 0.85` | `AI_CONF_MIN = 0.85` | 邻行有引用 |
| 1 | A 参数赋值 | `apps/main_line/chain_map.py:1288` | `AI_BORDERLINE_MIN = 0.5` | `AI_BORDERLINE_MIN = 0.5` | 邻行有引用 |
| 1 | D 调用参数 | `apps/main_line/confirm_chain.py:89` | `min(120 len ⇒ 120)` | `w = min(120, len(v))` | **无引用 ✗** |
| 1 | D 调用参数 | `apps/main_line/detect_new_theme.py:30` | `min(5 len ⇒ 5)` | `last5 = sum(v for _, v in pts[-min(5, len(pts)):])` | **无引用 ✗** |
| 1 | D 调用参数 | `apps/main_line/fusion_mainline.py:96` | `min(fund_p 0.3 ⇒ 0.3)` | `fund = min(fund_p, 0.3)   # 护盘: 资金分压低` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/intraday_crush.py:282` | `max * 1.5` | `s += max(0.0, min(2.0, float(f["vr"]) - 0.8)) * 1.5` | 邻行有引用 |
| 1 | C 倍率 | `apps/main_line/intraday_crush.py:284` | `max * 0.8` | `s += max(0.0, min(3.0, -float(f["below_vwap_pct"]))) * 0.8` | 邻行有引用 |
| 1 | D 调用参数 | `apps/main_line/intraday_crush.py:509` | `get(crash_pct 1.5 ⇒ 1.5)` | `crash_pct = float(c.get("crash_pct", 1.5) or 1.5)` | **无引用 ✗** |
| 1 | A 参数赋值 | `apps/main_line/mainline_confirm_state.py:30` | `LOOKBACK_DAYS = 40` | `LOOKBACK_DAYS = 40          # 主升确认资格回看窗(交易日)` | **无引用 ✗** |
| 1 | B 阈值比较 | `apps/main_line/mainline_confirm_state.py:79` | `days LtE 75` | `if (u - dd).days <= 75:` | **无引用 ✗** |
| 1 | D 调用参数 | `apps/main_line/position_class.py:139` | `min(250 len ⇒ 250)` | `N = min(250, len(ser))` | **无引用 ✗** |
| 1 | B 阈值比较 | `apps/main_line/position_class.py:150` | `pct Lt 0.4` | `"level": "low" if pct < 0.4 else ("high" if pct > 0.6 else "mid")}` | 邻行有引用 |
| 1 | C 倍率 | `apps/main_line/position_class.py:193` | `max * 0.97` | `if last < max(h1[1], h2[1]) * 0.97:` | **无引用 ✗** |
| 1 | B 阈值比较 | `apps/main_line/position_class.py:222` | `lscore GtE 0.6` | `sig["logic_strong"]=bool((lscore is not None and lscore>=0.6) or lv=="值得埋伏低吸")` | **无引用 ✗** |
| 1 | B 阈值比较 | `apps/main_line/replay_daily_2026.py:48` | `share Lt 0.8` | `return bool(len(sig_in) >= 2 and share < 0.8), bool(len(sig_in) <= 1), len(sig_in)` | **无引用 ✗** |
| 1 | B 阈值比较 | `apps/main_line/risk_gate.py:84` | `margin_pct GtE 0.8` | `if margin_pct is not None and margin_pct >= 0.8 and top_signal:` | **无引用 ✗** |
| 1 | B 阈值比较 | `apps/main_line/rotation_universe.py:240` | `top_in_share Lt 0.8` | `healthy = bool(len(sig_in) >= 2 and top_in_share < 0.8)` | **无引用 ✗** |
| 1 | B 阈值比较 | `apps/main_line/rotation_universe.py:241` | `top_in_share GtE 0.8` | `sucking = bool(len(sig_in) <= 1 or (sig_in and top_in_share >= 0.8))` | **无引用 ✗** |
| 1 | A 参数赋值 | `apps/main_line/scan_theme_gaps.py:68` | `min_r20 = 8.0` | `min_r20 = 8.0` | **无引用 ✗** |
| 1 | D 调用参数 | `apps/main_line/stock_confirm_judge.py:278` | `max(50 int ⇒ 50)` | `and _allc.get(_cn, 0) <= max(50, int(_tot * 0.20)):` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/systemic_risk.py:33` | `max * 0.97` | `if c[-1] < max(h1[1], h2[1]) * 0.97:` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/t_signal.py:39` | `vol_before * 0.8` | `no_vol = vol_after < vol_before * 0.8 if vol_before > 0 else False` | **无引用 ✗** |
| 1 | B 阈值比较 | `apps/main_line/test_sig_frac.py:29` | `share Lt 0.8` | `return bool(len(sig_in) >= 2 and share < 0.8), len(sig_in)` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/theme_leader_score.py:215` | `_pct * 0.5` | `lead = 0.5 * _pct(amts, ld[ts]["amt"]) + 0.5 * _pct(mvs, ld[ts]["mv"])   # 龙头度 ✓` | **无引用 ✗** |
| 1 | D 调用参数 | `apps/main_line/tmon_check.py:8` | `fetch_minute_bars(sh588170 m5 320 ⇒ 320)` | `b = fetch_minute_bars("sh588170", "m5", 320)` | 邻行有引用 |
| 1 | D 调用参数 | `apps/main_line/trend_channel.py:91` | `_f(WOLF_TREND_MULT 1.005 ⇒ 1.005)` | `"mult": _f("WOLF_TREND_MULT", 1.005),` | **无引用 ✗** |
| 1 | D 调用参数 | `apps/main_line/trend_channel.py:92` | `_f(WOLF_TREND_VOL_Z 1.5 ⇒ 1.5)` | `"vol_z": _f("WOLF_TREND_VOL_Z", 1.5),` | **无引用 ✗** |
| 1 | D 调用参数 | `apps/main_line/trend_channel.py:95` | `_f(WOLF_TREND_POS_MAX 0.8 ⇒ 0.8)` | `"pos_max": _f("WOLF_TREND_POS_MAX", 0.8),` | **无引用 ✗** |
| 1 | D 调用参数 | `apps/main_line/trend_channel.py:96` | `_f(WOLF_TREND_CHASE_MAX 0.03 ⇒ 0.03)` | `"chase_max": _f("WOLF_TREND_CHASE_MAX", 0.03),` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/trend_confirm.py:114` | `max * 1.0001` | `if i >= w0 + 10 and c[i] >= max(c[w0:i]) * 1.0001:` | **无引用 ✗** |
| 1 | D 调用参数 | `apps/main_line/trend_confirm_calibrate.py:75` | `max(prec'Add'rec 1e-09 ⇒ 1e-09)` | `f1 = 2 * prec * rec / max(prec + rec, 1e-9)` | **无引用 ✗** |
| 1 | D 调用参数 | `apps/main_line/trend_gate_calibrate.py:80` | `max(prec'Add'rec 1e-09 ⇒ 1e-09)` | `f1 = 2 * prec * rec / max(prec + rec, 1e-9)` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/tsell_days.py:16` | `prev_vol_avg * 0.7` | `if prev_low and lo<=prev_low*1.005 and prev_vol_avg>0 and vol<=prev_vol_avg*0.7 and cl>vwap: return ("254",lo,` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/tsell_fri_1400.py:22` | `prev_vol_avg * 0.7` | `if prev_low and lo<=prev_low*1.005 and prev_vol_avg>0 and vol<=prev_vol_avg*0.7 and cl>vwap: return ("254",lo,` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/tsell_fri_254.py:19` | `prev_vol_avg * 0.7` | `if prev_low and lo<=prev_low*1.005 and prev_vol_avg>0 and vol<=prev_vol_avg*0.7 and cl>vwap: return ("254",lo,` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/tsell_fri_confirm_only.py:19` | `prev_vol_avg * 0.7` | `if prev_low and lo<=prev_low*1.005 and prev_vol_avg>0 and vol<=prev_vol_avg*0.7 and cl>vwap: return ("254",lo,` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/tsell_fri_exit_backtest.py:24` | `prev_vol_avg * 0.7` | `if prev_low and lo<=prev_low*1.005 and prev_vol_avg>0 and vol<=prev_vol_avg*0.7 and cl>vwap:` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/tsell_fri_pnl.py:19` | `prev_vol_avg * 0.7` | `if prev_low and lo<=prev_low*1.005 and prev_vol_avg>0 and vol<=prev_vol_avg*0.7 and cl>vwap: return ("254",lo,` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/tsell_fri_rule.py:22` | `prev_vol_avg * 0.7` | `if prev_low and lo<=prev_low*1.005 and prev_vol_avg>0 and vol<=prev_vol_avg*0.7 and cl>vwap: return ("254",lo,` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/tsell_real_buy_backtest.py:17` | `prev_vol_avg * 0.7` | `if prev_low and lo <= prev_low*1.005 and prev_vol_avg>0 and vol <= prev_vol_avg*0.7 and cl>vwap:` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/tsell_struct_risk.py:19` | `prev_vol_avg * 0.7` | `if prev_low and lo<=prev_low*1.005 and prev_vol_avg>0 and vol<=prev_vol_avg*0.7 and cl>vwap: return ("254",lo,` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/tsell_struct_risk.py:47` | `prev_vol * 0.8` | `if prev_vol>0 and day_vol<prev_vol*0.8: return True` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/tsell_struct_risk.py:48` | `prev_vol * 0.9` | `if c>=dm*0.995 and prev_vol>0 and day_vol<prev_vol*0.9: return True` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/tsell_struct_sum.py:19` | `prev_vol_avg * 0.7` | `if prev_low and lo<=prev_low*1.005 and prev_vol_avg>0 and vol<=prev_vol_avg*0.7 and cl>vwap: return ("254",lo,` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/tsell_struct_sum.py:46` | `prev_vol * 0.8` | `return bool(c<ma5 or (prev_vol>0 and day_vol<prev_vol*0.8) or (c>=dm*0.995 and prev_vol>0 and day_vol<prev_vol` | **无引用 ✗** |
| 1 | C 倍率 | `apps/main_line/wave_agent.py:207` | `min * 1.05` | `if midmax >= min(other[1], last[1]) * 1.05:` | **无引用 ✗** |
| 1 | A 参数赋值 | `apps/main_line/wolf_entry_filters.py:39` | `MA_LINE_MIN_CLOSES = 144` | `MA_LINE_MIN_CLOSES = 144          # 与 rotation_switch_arm 一致：算得出 144 线所需的最少 K 线` | **无引用 ✗** |
| 1 | A 参数赋值 | `apps/main_line/wolf_entry_filters.py:41` | `DOMAIN_MIN = 6` | `DOMAIN_MIN = 6                    # 分位所需最少域内样本（不足 → 不判/放行）` | **无引用 ✗** |
| 1 | A 参数赋值 | `apps/main_line/wolf_theme_vol_fund.py:65` | `ACT_DAYS = 5` | `ACT_DAYS = 5        # 他："最近一周"` | **无引用 ✗** |
| 1 | A 参数赋值 | `apps/main_line/wolf_theme_vol_fund.py:67` | `FUND_DAYS = 5` | `FUND_DAYS = 5       # 他："资金没有5日连续流出的"` | **无引用 ✗** |
| 1 | A 参数赋值 | `backend/app/api/backtest.py:2107` | `slip_pct = 0.0003` | `slip_pct = 0.0003` | **无引用 ✗** |
| 1 | C 倍率 | `backend/app/api/backtest.py:2112` | `vol_ratio * 0.003` | `slip_pct += vol_ratio * 0.003` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/backtest.py:2117` | `move_pct Gt 9.5` | `if move_pct > 9.5:` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/backtest.py:2121` | `move_pct Gt 5` | `elif move_pct > 5:` | **无引用 ✗** |
| 1 | A 参数赋值 | `backend/app/api/backtest.py:2126` | `slip_pct = 0.0003` | `slip_pct = 0.0003  # fallback: 基础滑点` | **无引用 ✗** |
| 1 | C 倍率 | `backend/app/api/backtest.py:2171` | `sell_amount * 0.001` | `stamp_tax = sell_amount * 0.001` | **无引用 ✗** |
| 1 | C 倍率 | `backend/app/api/backtest.py:2172` | `sell_amount * 0.0005` | `commission = sell_amount * 0.0005` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/backtest.py:2666` | `days_collected Lt 5` | `while days_collected < 5 and cursor.year > trade_dt.year - 5:` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/backtest.py:2685` | `days_collected Lt 5` | `if days_collected < 5:` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/backtest.py:2719` | `change_pct LtE 8` | `elif change_pct <= 8:` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/backtest.py:2722` | `volume_ratio Gt 1.5` | `vol_ok = (volume_ratio is not None and volume_ratio > 1.5)` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/backtest.py:2776` | `volume_ratio Gt 1.5` | `"volume_ratio_ok": (volume_ratio is not None and volume_ratio > 1.5) if 2 < change_pct <= 8 else None,` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/backtest.py:3034` | `get_sector_intraday_bias(sector_name end_dt hh mm concept 8 ⇒ 8)` | `ib = local_data.get_sector_intraday_bias(` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/backtest.py:3152` | `get_sector_intraday_bias(sector_name end_dt hh mm industry 8 ⇒ 8)` | `ib = local_data.get_sector_intraday_bias(` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/backtest.py:4626` | `change_pct Gt 8` | `if change_pct > 8:` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/backtest.py:4629` | `change_pct Gt 5` | `elif change_pct > 5:` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/backtest.py:4987` | `vol_ratio Gt 0.8` | `"passed": vol_ratio > 0.8,` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/backtest.py:5115` | `days_count Lt 5` | `while days_count < 5:` | **无引用 ✗** |
| 1 | A 参数赋值 | `backend/app/api/direction.py:28` | `MONEYFLOW_MEDIAN_DAYS = 20` | `MONEYFLOW_MEDIAN_DAYS = 20  # 中位数兜底窗口` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/etf.py:190` | `min(50 pct_1d'Add'2'Mult'12.5 ⇒ 50)` | `momentum_score = max(0, min(50, (pct_1d + 2) * 12.5))` | **无引用 ✗** |
| 1 | C 倍率 | `backend/app/api/etf.py:196` | `sector_score * 0.6` | `catalyst_score = sector_score * 0.6 + momentum_score * 0.4` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/etf.py:198` | `catalyst_score GtE 70` | `tag = '🟢' if catalyst_score >= 70 else ('🟡' if catalyst_score >= 55 else '🔴')` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:157` | `HTTPException(503 Tushare 模块不可用 ⇒ 503)` | `raise HTTPException(status_code=503, detail="Tushare 模块不可用")` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/indicator.py:489` | `max_profit_pct GtE 5` | `if max_profit_pct >= 5 and current_price < cost * 0.99:` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/indicator.py:521` | `days_since_high GtE 13` | `if not signal and days_since_high is not None and days_since_high >= 13 and not is_new_high:` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/indicator.py:541` | `days_since_high GtE 8` | `if days_since_high is not None and days_since_high >= 8:` | **无引用 ✗** |
| 1 | C 倍率 | `backend/app/api/indicator.py:575` | `high'Sub'diff'Mult'0.5 * 1.02` | `elif current_price <= (high - diff * 0.5) * 1.02:` | **无引用 ✗** |
| 1 | A 参数赋值 | `backend/app/api/indicator.py:1115` | `ratio = 999.0` | `ratio = 999.0` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:1228` | `get_sector_flow(concept main_net 50 ⇒ 50)` | `all_concepts = get_sector_flow("concept", sort_by="main_net", top_n=50)` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/indicator.py:1420` | `atr_pct Lt 4` | `elif atr_pct < 4:` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/indicator.py:1422` | `atr_pct Lt 6` | `elif atr_pct < 6:` | **无引用 ✗** |
| 1 | A 参数赋值 | `backend/app/api/indicator.py:1673` | `_p3_cash_floor_pct = 25.0` | `_p3_cash_floor_pct = 25.0` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:2533` | `min(downgrade_multiplier 0.3 ⇒ 0.3)` | `downgrade_multiplier = min(downgrade_multiplier, 0.3)` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:2540` | `min(downgrade_multiplier 0.5 ⇒ 0.5)` | `downgrade_multiplier = min(downgrade_multiplier, 0.5)` | 邻行有引用 |
| 1 | D 调用参数 | `backend/app/api/indicator.py:2566` | `min(downgrade_multiplier 0.5 ⇒ 0.5)` | `downgrade_multiplier = min(downgrade_multiplier, 0.5)` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:2596` | `min(downgrade_multiplier 0.5 ⇒ 0.5)` | `downgrade_multiplier = min(downgrade_multiplier, 0.5)` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:2603` | `min(downgrade_multiplier 0.5 ⇒ 0.5)` | `downgrade_multiplier = min(downgrade_multiplier, 0.5)` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:2627` | `min(downgrade_multiplier 0.5 ⇒ 0.5)` | `downgrade_multiplier = min(downgrade_multiplier, 0.5)` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:2638` | `min(downgrade_multiplier 0.5 ⇒ 0.5)` | `downgrade_multiplier = min(downgrade_multiplier, 0.5)` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:2644` | `min(downgrade_multiplier 0.5 ⇒ 0.5)` | `downgrade_multiplier = min(downgrade_multiplier, 0.5)` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/indicator.py:2663` | `kdj_cross_mult LtE 0.5` | `if kdj_cross_mult <= 0.5 and layer1_passed:` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:2718` | `min(downgrade_multiplier 0.5 ⇒ 0.5)` | `downgrade_multiplier = min(downgrade_multiplier, 0.5)` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:2722` | `min(downgrade_multiplier 0.7 ⇒ 0.7)` | `downgrade_multiplier = min(downgrade_multiplier, 0.7)` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:2748` | `min(downgrade_multiplier 0.5 ⇒ 0.5)` | `downgrade_multiplier = min(downgrade_multiplier, 0.5)` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:2777` | `min(downgrade_multiplier 0.5 ⇒ 0.5)` | `downgrade_multiplier = min(downgrade_multiplier, 0.5)` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:2835` | `min(downgrade_multiplier 0.5 ⇒ 0.5)` | `downgrade_multiplier = min(downgrade_multiplier, 0.5)` | 邻行有引用 |
| 1 | B 阈值比较 | `backend/app/api/indicator.py:2926` | `downgrade_multiplier LtE 0.5` | `elif downgrade_multiplier <= 0.5:` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/indicator.py:2955` | `change_pct LtE 5` | `elif change_pct <= 5:` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/indicator.py:2958` | `change_pct LtE 8` | `elif change_pct <= 8:` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/indicator.py:2962` | `volume_ratio Gt 1.5` | `vol_ok = volume_ratio > 1.5` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:3155` | `HTTPException(503 tushare 库未安装，请 pip i ⇒ 503)` | `raise HTTPException(status_code=503, detail="tushare 库未安装，请 pip install tushare")` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:3244` | `HTTPException(503 tushare 库未安装，请 pip i ⇒ 503)` | `raise HTTPException(status_code=503, detail="tushare 库未安装，请 pip install tushare")` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/indicator.py:3385` | `vol_ratio Gt 0.8` | `"passed": vol_ratio > 0.8,` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:3448` | `_fetch_tushare_moneyflow(symbol 5 ⇒ 5)` | `mf = _fetch_tushare_moneyflow(symbol, days=5)` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:3639` | `min(5 len ⇒ 5)` | `ma5 = sum(closes[-min(5, len(closes)):]) / min(5, len(closes))` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:3667` | `_calc_rsi_tushare(closes 6 ⇒ 6)` | `rsi = _calc_rsi_tushare(closes, 6)` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/indicator.py:3677` | `_fetch_tushare_moneyflow(symbol 5 ⇒ 5)` | `mf = _fetch_tushare_moneyflow(symbol, days=5)` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/main_wave.py:61` | `HTTPException(404 涨停池为空（akshare 不可用或当日 ⇒ 404)` | `raise HTTPException(status_code=404, detail="涨停池为空（akshare 不可用或当日无数据）")` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/market.py:410` | `HTTPException(503 tushare 库未安装，请 pip i ⇒ 503)` | `raise HTTPException(status_code=503, detail="tushare 库未安装，请 pip install tushare")` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/market.py:749` | `HTTPException(503 tushare 库未安装，请 pip i ⇒ 503)` | `raise HTTPException(status_code=503, detail="tushare 库未安装，请 pip install tushare")` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/market.py:838` | `HTTPException(503 tushare 库未安装，请 pip i ⇒ 503)` | `raise HTTPException(status_code=503, detail="tushare 库未安装，请 pip install tushare")` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/market.py:1035` | `minute Lt 15` | `is_intraday = now.hour < 15 or (now.hour == 15 and now.minute < 15)` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/market.py:1115` | `HTTPException(503 tushare 库未安装，请 pip i ⇒ 503)` | `raise HTTPException(status_code=503, detail="tushare 库未安装，请 pip install tushare")` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/market.py:1240` | `minute Lt 15` | `is_intraday = now.hour < 15 or (now.hour == 15 and now.minute < 15)` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/market.py:1280` | `HTTPException(503 tushare 库未安装 ⇒ 503)` | `raise HTTPException(status_code=503, detail="tushare 库未安装")` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/market.py:1361` | `HTTPException(503 tushare 库未安装，请 pip i ⇒ 503)` | `raise HTTPException(status_code=503, detail="tushare 库未安装，请 pip install tushare")` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/market.py:1874` | `max_consecutive GtE 5` | `if max_consecutive >= 5:` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/market.py:2147` | `HTTPException(503 tushare 库未安装，请 pip i ⇒ 503)` | `raise HTTPException(status_code=503, detail="tushare 库未安装，请 pip install tushare")` | **无引用 ✗** |
| 1 | A 参数赋值 | `backend/app/api/news.py:90` | `sentiment_score = 80.0` | `sentiment_score = 80.0` | **无引用 ✗** |
| 1 | A 参数赋值 | `backend/app/api/news.py:92` | `sentiment_score = 20.0` | `sentiment_score = 20.0` | **无引用 ✗** |
| 1 | A 参数赋值 | `backend/app/api/news.py:94` | `sentiment_score = 50.0` | `sentiment_score = 50.0` | **无引用 ✗** |
| 1 | D 调用参数 | `backend/app/api/news.py:137` | `get(score 50 ⇒ 50)` | `score=sentiment_data.get("score", 50),` | **无引用 ✗** |
| 1 | B 阈值比较 | `backend/app/api/portfolio.py:601` | `max_weight Gt 50` | `if max_weight > 50:` | **无引用 ✗** |
