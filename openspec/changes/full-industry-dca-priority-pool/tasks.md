## 1. 数据与配置层

- [x] 1.1 在 `golden_pit_industry_service.py` 新增 `INDUSTRY_POOL`（首版 24 个有贪婪+场内 ETF 收益代理的 A 股行业：id/名称/greed_code/etf_code/priority/max_total_pct/min_days_in_pit/proxy_type）与行业贪婪历史加载（复用 `funds-greed/fund/{code}`；实现落于 `golden_pit_industry_service.py` 而非 `golden_pit_config.py`，避免 config 层引入数据源依赖）
- [x] 1.2 `golden_pit_sector_config` 新增配置键：`industry_pool_enabled`(bool)、`industry_pool`(json)、`cash_min_pct`(number 默认0.2)、`industry_pit_pct`(0.15)、`industry_drawdown_pct`(0.20)、`industry_entry_cap`(0.85)；seed + 弹窗可改
- [x] 1.3 行业收益序列加载（tushare `fund_daily`，3600s TTL 缓存）；行业行由内置池 JSON 直供（`industry_pool` 配置复用 max_total/priority），未落 `golden_pit_etf_config` 表——回测与生产代理一致

## 2. 信号与资金池裁决

- [x] 2.1 行业信号计算：250 日贪婪分位 + 60 日超跌（双条件 AND；贪婪历史<20 天仅价格触发；分位>entry_cap 过热过滤）——纯函数 `industry_signal` + 单测
- [x] 2.2 资金池裁决纯函数 `ration(plans, available_cash)`：按 priority 从高到低逐个分配计划金额，额度滚动次日（leftover），返回 {allocations, cut_items}——单测覆盖并发超现金/现金充足/现金下限
- [x] 2.3 坑间资金流转：出场→防御承接（`DEFENSE_CODES` 复用防御组合代码）；现金不足→额度滚动次日（leftover）。生产真实赎回回补并入 5.3 灰度执行

## 3. DCA 调度接入

- [x] 3.1 行业轨引擎 `advance_industry_windows`（in_pit≥2 开窗、15 日 DCA 前 5 日等权、TP+15%/时间止损 60 日/满仓止损 -10%、dry-run 推进 + execute 返回指令）；`golden_pit_dca_service` 返回 `industry_monitor`。真实下单并入 5.3 灰度执行
- [x] 3.2 资金池裁决接入：`available = max(0, nav - cash_floor)` 扣除现金下限，裁剪项入 `cut_items`（日志 + 资金池视图）
- [x] 3.3 全行业现状只读接口：GET 黄金坑 status 增加 `industry_monitor.industries[]`（greed_pct/drawdown/in_pit/window_day/planned_amount/actual_amount/total_invested）与 `cash_pool` 视图（120s 缓存）

## 4. 前端与报告

- [x] 4.1 黄金坑页新增"全行业监测 + 资金池"面板（行业贪婪分位/超跌/in-pit 列表 + 资金池计划/实际/裁剪），配置弹窗支持 `industry_pool_enabled`/`cash_min_pct` 编辑
- [x] 4.2 报告/通知：晨报/日报 `format_monitor_text` 行业块（QQ 通知兼容）

## 5. 验证与上线

- [x] 5.1 回测复验：全行业 DCA+优先级+防御承接组合收益 vs 科创50 躺平/现有 tech7（data/backtest/_industry_pool_backtest.py），样本 2025-01~2026-08；基准参数 +36.14%（MDD -9.3%，24 坑，胜率 54%），跑赢沪深300(+21.5%)/24行业等权(+7.0%)/防御(+1.9%)，跑输科创50 躺平(+81.7%)；激进参数（回撤15%+止盈10%+止损5%）+55.84%；分行业 tp×stop 敏感性扫描结论：9/24 行业无完成坑参数无效、有坑行业区间差仅 1-6pp（噪音级）→ 维持统一参数 + 优先级裁决（详见 design.md D6 / 分行业参数敏感性）
- [ ] 5.2 dry-run 一周：输出计划 vs 实际比对，检查裁决边界（并发、现金耗尽、防御回补）
- [ ] 5.3 灰度：`industry_pool_enabled=true`（行业 max_total 低额起步），异常一键关停回滚；文档更新
