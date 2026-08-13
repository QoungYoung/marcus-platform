## 1. 数据与配置层

- [ ] 1.1 在 `golden_pit_config.py` 新增 `INDUSTRY_POOL`（首版 24 个有贪婪+场内 ETF 收益代理的 A 股行业：id/名称/greed_code/proxy_type/etf_code|nav_code/priority/max_total/min_days_in_pit）与行业贪婪历史加载（复用 `funds-greed/fund/{code}`）
- [ ] 1.2 `golden_pit_sector_config` 新增配置键：`industry_pool_enabled`(bool)、`industry_pool`(json)、`cash_min_pct`(number 默认0.2)、`industry_pit_pct`(0.15)、`industry_drawdown_pct`(0.20)、`industry_entry_cap`(0.85)；seed + 弹窗可改
- [ ] 1.3 `golden_pit_etf_config` 支持行业行（fund_code=`industry_<id>`，etf/nav 代理，max_total/priority 复用）；行业收益序列加载（tushare `fund_daily`/`fund_nav`，TTL 缓存）

## 2. 信号与资金池裁决

- [ ] 2.1 行业信号计算：250 日贪婪分位 + N 日超跌（双条件 AND；贪婪历史<20 天仅价格触发；分位>entry_cap 过热过滤）——纯函数 + 单测
- [ ] 2.2 资金池裁决纯函数 `ration(plans, available_cash)`：按 (tier, priority) 从高到低逐个分配计划金额，额度滚动次日，返回 {actual, cut_items}——单测覆盖并发超现金/现金充足/现金下限
- [ ] 2.3 坑间资金流转：行业出场→防御承接（复用 `DEFENSE_TAKEOVER_WEIGHTS`）；新坑现金不足→按防御持仓比例赎回回补（扩展 `_sell_defense_on_reentry`）

## 3. DCA 调度接入

- [ ] 3.1 `golden_pit_dca_service` 主循环扩展双轨：`industry_pool_enabled=true` 时把 in-pit 行业并入当日候选（window 标识 `industry/<id>`），复用 DCA 窗口/权重/兜底/进度逻辑
- [ ] 3.2 调度内接入资金池裁决：汇总指数+行业计划金额→可用现金扣除下限→分配实际金额；被裁剪项记录日志与资金池视图
- [ ] 3.3 全行业现状只读接口：GET 黄金坑 status 增加 `industries[]`（greed_pct/drawdown/in_pit/window_day/planned/actual/total_invested）与 `cash_pool` 视图

## 4. 前端与报告

- [ ] 4.1 黄金坑页新增"全行业监测 + 资金池"面板（行业贪婪分位/超跌/in-pit 列表 + 资金池计划/实际/裁剪），配置弹窗支持 `industry_pool`/`cash_min_pct` 编辑
- [ ] 4.2 报告/通知：DCA 摘要包含行业定投明细与资金池裁剪说明（QQ 通知兼容）

## 5. 验证与上线

- [x] 5.1 回测复验：全行业 DCA+优先级+防御承接组合收益 vs 科创50 躺平/现有 tech7（data/backtest/_industry_pool_backtest.py），样本 2025-01~2026-08；基准参数 +36.14%（MDD -9.3%，24 坑，胜率 54%），跑赢沪深300(+21.5%)/24行业等权(+7.0%)/防御(+1.9%)，跑输科创50 躺平(+81.7%)；激进参数（回撤15%+止盈10%+止损5%）+55.84%；分行业 tp×stop 敏感性扫描结论：9/24 行业无完成坑参数无效、有坑行业区间差仅 1-6pp（噪音级）→ 维持统一参数 + 优先级裁决（详见 design.md D6 / 分行业参数敏感性）
- [ ] 5.2 dry-run 一周：输出计划 vs 实际比对，检查裁决边界（并发、现金耗尽、防御回补）
- [ ] 5.3 灰度：`industry_pool_enabled=true`（行业 max_total 低额起步），异常一键关停回滚；文档更新
