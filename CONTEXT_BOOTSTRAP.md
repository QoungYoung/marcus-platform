# 新上下文启动包（狼大策略复制 / 恢复指引）

> 更新：2026-09-03 晚（用户切换上下文前，HEAD commit a86fcee）。

## 0. 先读这几份
1. 仓库根 WOLF_TASKS_OVERVIEW.md —— 任务总览（含 09-03 新增 P2 宏观 v2 / P2 Gate 接入 / 拥挤 PIT / 买点对齐）。
2. 仓库根 CONTEXT_BOOTSTRAP.md（本文件）。
3. dsh-memoir(memoir_read)：09-02/09-03 大量记录；直接搜关键词：crowding PIT、macro_state、P2 Gate、wolf dip、E06-E13。
4. 服务器 /opt/marcus-platform/data/wave_config.md（浪型 v6 冻结）。

## 1. 已完成（不用重做，结论在文档/记忆）
- 主线/浪型/高低位/确定性/做T/轮动+拥挤PIT/风控。
- **P2 宏观 v2**：macro_state 采集器 15:06（akshare CN/US 2-30Y + 新浪 DXY + 两融/GJD/北向）；Wolf 四类开关 → trade_graph 宏观上下文；A/B 10/10（龙虎榜 top_list+top_inst 修 M10）。
- **P2 Gate 接入交易 Step1+2**：p2_entry_gate 进 check_entry_filters → auto 通道也硬拦；方向感知 map(config)；p2_gate_log + 15:20 每日报告。
- 拥挤过滤个股级 PIT v2：黑名单 1045→17；E06-E13 前向验证。
- 买点对齐：MA/KDJ/RSI/CCI/射击之星/午后禁开 = 软约束（LEGACY_TECH_GATES=1 回退）；A/B 报告 docs/ab-wolf-gates-e01e15.md。

## 2. 生产状态（2026-09-03）
- stock 账户：药明底仓 100 股；做T 5 腿（249/250/252/253/254）；auto_trade 5 任务 enabled。
- 调度 tasks=28：周一主线/浪型/position；周日入池/宇宙；季度基金拥挤；工作日 15:05 systemic、15:06 macro_state、15:20 p2_gate_daily_report。
- 候选拦截链：risk_flags → crowding_blacklist(个股级) → P2 Gate(wave/systemic/macro) → 技术/资金（Wolf 对齐软约束）→ calc_position。
- 回退开关：LEGACY_TECH_GATES=1；P2_GATE_MODE=0。

## 3. 数据源路径（新增）
- 狼大语料 xlsx（到 2026-08-14）；NGA tid=47288722 uid=150058 pages 34-38（08-31~09-02）。
- 收益率：worker 已装 akshare，ak.bond_zh_us_rate() 一表含 CN/US 2/5/10/30Y（1990 起）；DXY 实时=新浪 hq.sinajs.cn/list=DINIW（历史待自建）；FRED/Yahoo/东财在服务器不可用。
- 龙虎榜：tushare pro.top_list + top_inst（03-18 实证：深股通专用净卖 -6.1 亿）。
- P2 Gate 日志：data/p2_gate_log.jsonl（backend 每次命中追加）；每日聚合 data/p2_gate_daily_report.json。

## 4. 生产注意事项
- 只有狼大做T可操作 stock 账户；做T卖出保留 100 底仓；止损 DYNAMIC_ONLY 只读。
- prompt 权威源 backend/app/db/prompt_seeds.py；改后 reseed。
- tasks.yaml 本地 config/ 权威；改后同步服务器并 restart marcus-worker（跑长任务勿重启）。
- 重启惯例：backend/app|jobs 改动→marcus-worker；backend/api|models 改动→marcus-backend；apps/main_line 脚本→/app/apps bind mount 生效（worker）。
- 新模块坑：docker exec 跑 python 需要 -i（heredoc）；detached 日志在容器 /tmp（用 docker exec cat）；杀容器进程用 /proc 容器内 PID。

## 5. 下一步候选（详见 WOLF_TASKS_OVERVIEW §3）
1. P2 Gate Step3：观察 2 周 p2_gate_log → 校准阈值 → 定稿（默认已 =1）。
2. P2 宏观收尾：DXY 历史快照自建、崩盘清单源（期指/30Y 放量/券商破位）、板块级外资/龙虎榜、policy_floor 日历。
3. rotation_quadrant_history 13 时点真 PIT 重跑（基建已具备，工作量小）。
4. 主线科技子类粒度（用户暂缓 P1）。
5. P3 三仓档位模型（与"t_only/defense 加厚底仓"缺口直接相关）。
