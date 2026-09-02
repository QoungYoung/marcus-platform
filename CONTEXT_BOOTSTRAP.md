# 新上下文启动包（狼大策略复制 / 恢复指引）

> 用途：新会话/新上下文接手「狼大交易策略复制」时快速恢复。更新：2026-09-03（早，用户切换上下文前）。

## 0. 先读这几份（恢复上下文）
1. 仓库根 `WOLF_TASKS_OVERVIEW.md` —— 大周期/已完成/当前状态总览（2026-09-03 更新：含主线内轮动闭环 + P2 风控主体）。
2. 仓库根 `CONTEXT_BOOTSTRAP.md`（本文件）—— 数据源/生产注意事项。
3. 项目持久记忆 dsh-memoir(memoir_read) —— 最近大量 09-02/09-03 记录：rotation 验证→细分宇宙→真实公募拥挤→risk_gate/risk_flags→systemic 开关→材料买点→双维回测。
4. 服务器 /opt/marcus-platform/data/wave_config.md（浪型 v6 冻结）。

## 1. 已完成模块（不用重读，结论在文档/记忆）
- 主线融合 81% + OOS 5/7=71%（生产 v1 权重）｜浪型 v6(75%/83%)｜高低位 v2(79%)｜确定性门槛 confirm_chain｜做T三档+黄线+底仓保护｜执行层狼大化（prompt/trade_graph/扫描诊断）。
- **P2 主线内轮动/产业链形态 ✅ 第一轮闭环（09-02/09-03）**：语料→case→wave 23/23→rotation_gate v2（双绿）→trade_graph 轮动门控→细分宇宙+真实公募拥挤(Q2)→双维打分→LOW/候选硬过滤→每周/季度/每日调度；遗留 3 项（材料买点 material_entry / 双维回测 backtest_rotation_quadrant / 拥挤黑名单 crowding_blacklist）全部完成。
- **P2 风控主体 ✅（09-03）**：risk_gate v1 + risk_flags DB(forecast/express/ST) + check_entry_filters 硬拦（业绩雷 E2E blocked）+ crowding 硬拦 + systemic_risk（银行双头/大光破位大黑K，15:05 采集）+ Pi prompt"先过滤后暴雷校验"。剩余：公告类 source=ai（等 news）。

## 2. 生产状态（2026-09-03）
- stock 账户：药明康德底仓 100 股，现金 ~234k；做T 5 条持续腿（249/250/252/253/254）；auto_trade 5 任务 enabled。
- 调度 tasks=26：
  - 周一 8:00 main_line_judge → 8:10 wave_judge → 8:20 position_judge（链 rotation_universe 刷新黑名单）
  - 周日 8:00 stock_pool_refresh → 8:05 rotation_universe_classify（首次全量/增量+清理旧概念）
  - 季度 1/4/7/10 月 25 日 8:30 fund_crowding_refresh（真实基金持仓+拥挤度）
  - 工作日 15:05 systemic_risk_monitor（银行/科创50/大光三股）
- 候选拦截链：wave gate → rotation gate → risk_flags(硬拦) → crowding_blacklist(硬拦) → check_entry_filters → calc_position；做T T仓走 t_monitor 单独通道。
- **改风险/拥挤依赖数据**：risk_flags 候选即时查 `build_risk_flags.py --stocks ...`；拥挤黑名单 `rotation_universe.py` 生成（position_judge 链刷新）。

## 3. 数据源路径
- 狼大语料 xlsx（到 2026-08-14）；NGA 主帖 tid=47288722（uid=150058, nga_read_post scope=author pages 34-38=08-31~09-02）；最近态度见记忆备注。
- 分钟：brze/tu.brze.top（1/5/15min 历史全通）、腾讯 qt(实时+VWAP)、腾讯/新浪 m5；个股5min data/stock_5min_{603259,603678,000725,002384,688072}.json。
- 真实基金：gyzcloud POST fund_portfolio/fund_share → DB fund_portfolio_holdings(Q2 20260630, 642行)；concept/股票映射 DB stock_concept_map(11.2万)；业绩 forecast/express 可用、anns 403。

## 4. 生产注意事项
- 只有狼大做T可操作 stock 账户；t 账户被网关白名单拒；做T卖出保留 100 底仓（代码硬拦）；止损监控 DYNAMIC_ONLY 只读。
- prompt 权威源 backend/app/db/prompt_seeds.py；改后需 FORCE_RESEED_PROMPTS 式 reseed（DB id=37）。
- tasks.yaml 本地 config/ 权威；改动同步服务器并 docker restart marcus-worker；**跑长任务期间勿重启 worker（曾杀 batch）**。
- 重启惯例：backend/app|jobs 改动→marcus-worker；backend/api 改动→marcus-backend；apps/main_line 脚本→/app/apps bind mount 生效（worker）。
- 服务器 81.70.44.68 (marcus) /opt/marcus-platform；postgres marcus-trading；docker compose -f docker/docker-compose.yml up -d backend worker。

## 5. 下一步候选（详见 WOLF_TASKS_OVERVIEW §3）
1. P2 宏观/机构行为（唯一未深挖大模块：两融/30年国债/GJD/政策语料分析→逻辑→state）
2. 季度基金持仓回填 2025Q4~2026Q2 → rotation 双维回测真点内化
3. 公告类 source=ai（等 news）
4. P3 三仓档位模型 / P1 主线收尾（用户已暂缓 P1）
