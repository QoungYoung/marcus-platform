# 狼大交易策略复制 — 任务总览（大周期 / 小周期 / 已完成）

> 生成：2026-09-01，更新：2026-09-02(晚)。目标：逆向复刻狼大(-阿狼-)完整 A 股交易策略。
> 已完成：主线判定、浪型级别判定(冻结v6)、高低位分类(核心+共振)、**做T体系(狼大做T信号+生产落地)**。

## 1. 已完成部分

### 1.1 主线判定（main_line_judge）✅
- 研报 report_rc → dsh agent 产业催化分 catalyst(0-1) → 候选(catalyst>=0.7) → 确认(候选+动量/资金转强) 两级。
- 排名 z(catalyst)*1 + 0.3*z(momentum)。产物 data/main_line_state.json；调度每周一 8:00。
- **历史重放(2026-09-01)**：改造 report_rc→research_report(有历史研报)+--date/--out/--window 参数；22个标注日期重放(30天窗口) **15/27=56%**(代理60%)——研报催化≠狼大主线判断，需三信号融合(w1研报catalyst+w2资金持续性+w3相对强度)。

### 1.2 浪型级别判定（wave_agent）✅ 冻结 v6
- 两级 schema：level(d1..d5/down) + sub_level(3-x/4-x/失败5/ABC/B反/C杀/W底/双头M顶/衰竭) + operation(build/t_only/side/defense/exit)。
- 特征：价格结构+量能(vol_ratio/z20/pct120)+两融+北向+GJD超额+GJD份额净申赎+历史锚点+主线。
- gate：_wave_level_gate 按(level,sub_level)→operation→gate；defense/exit 硬拦不建仓。
- **wave_context ③已落地(09-02, commit 7952611)**：喂 Pi 的 wave_context 已接 agent 两级输出(level/sub_level/operation/gate+操作指令)，与安全门同源；缺 wave_state.json 回退 rule-based。
- 回测：方向 75% / 级别族 83%。配置固化 v6（wave_config.md + wave_version.json）。调度每周一 8:10。

### 1.3 高低位分类（position_class）✅ 核心+共振
- 东财概念(441+)：结构分级(高/中/低+趋势) + 位置/量能/资金流/题材 + 指数大级别锚 + 高位三档操作。
- 共振(resonance)：低位+资金流+逻辑→低吸埋伏；高位+资金流出/量价顶→减仓/只做T；破MA60→防御清仓。
- 系统性回测(2026-09-01)：LOW+资金流入 20日命中80%(n=20) 但需结构确认；HIGH/MID 20日区分弱(0.49/0.50)；量能硬接入有效(高位放量=顶确认, 地量=止跌)。
- **一致性测试链路**：v1 50%→100%(G1结构+G2相对主线位置) 但过拟合；**v2 扩标注集(111条)+双人复核：主线60%/高低位75%→79%**（真实水平）。

### 1.4 确定性门槛（confirm_chain）✅ P1
- 确认链 S1缩量止跌→S2结构→S3放量突破→S4站稳 + F1-F4 假信号（硬抄底0.80 vs 确认组0.94/0.86, 指数确认60d hit 1.00）。

### 1.5 做T体系（狼大做T信号 + 生产落地）✅ 2026-09-02
- **分时T出(7-29原话)**：超跌反弹放量→第一次分时高点→停量→二次拉升无量不过前高→T出。5min回测(184天指数) 11触发 91% 不再创新高。生产：high_sell 条件(250)。
- **正T买点(1-12『利用盘中大盘带下来的机会做正T』)**：指数5min当日盘中回撤 dd∈[2%,3%)→个股低吸。纯规则回测(5股×184天, brze数据)：T+1 +3.15% hit0.75 / T+2 +4.92% hit0.78，分半稳定(前+0.90%/后+4.23%)，dd≥3%失效(系统性风险)。生产：low_buy 条件(249, index.intraday_dd∈[2,3))。
- **黄线跌破离场(8-04『黄线跌破直接走』)**：现价<分时均价线(VWAP=成交额/量)即离场，替代-3%固定止损。腾讯qt average 与 brze 权威VWAP 交叉验证差异0.014%。生产：custom sell 条件(252, quote.vwap_break)。
- **T1缩转放 语义修正(2026-09-02 重大发现)**：个股5min验证**无预测力**(药明86%/火炬95%触发率, 前向收益≈0；指数91%命中午休伪信号已排除)。根因：缩转放是**转折点信号，方向由位置决定**(8-04高位=卖/T点、8-12低位=买/止跌)，载体是**指数/板块量能**(跟前一天比)，不是个股日内5min形态。→ t1_shrink_expand **暂缓不恢复**作自动买腿。
- **止损监控改造**：STOP_LOSS_DYNAMIC_ONLY=1 只读动态离场距离监控（黄线VWAP距离+分时T出前高距离），不自动卖；旧8条止损距离体系已屏蔽；卖腿保留100底仓(狼大『底仓不卖』铁律)。

### 1.6 交易执行层狼大化改造（2026-09-02 下午）
- **做T底仓保护(代码硬拦)**：trades.py 卖出时做T标的(有active t_conditions)最多卖持仓-100、持仓≤100拒绝——Pi place_order 卖出路径不经 t_gateway 白名单，此保护堵住"Pi卖光做T底仓"风险；prompts 表 id=37 同步加"底仓不卖"条款。
- **auto_trade 5任务已恢复 enabled**（09-02 下午，此前停用）——Pi 自主交易回来，受做T底仓保护约束。
- **盘中扫描(jobs/market_scan.py) 接 🐺狼大视角**：报告顶部注入主线/浪型(level·sub_level·operation)/个股确认比例/高低位埋伏名单/做T提示；清理死代码 521 行(16.2%, commit 0480af2)。
- **盘前诊断(jobs/morning_diagnosis.py V2.1🐺)**：加狼大视角块；删除旧框架"震荡市/趋势市投票+持仓1-3天"结论(与狼大浪型operation矛盾)。
- **交易提示词(prompt_seeds.py→DB id=37 reseed)**：SOP前加【第零步：浪型策略(交易主基调)】每报告必输出；删月度表"盈亏比目标1:1.5"；calc_position 静态止损 8%→3%狼大逻辑止损(黄线由t_monitor 252执行)；prompt_seeds 是权威源(FORCE_RESEED_PROMPTS=true 覆盖DB，改DB不改源=重建丢)。
- **trade_graph 震荡/趋势注入对齐月度门控**：regime文本改"日度指标非月度regime"，震荡日短期层只做T不新开(删旧"建仓60%/加仓40%"时段指令)；trend分支不变。
- **部署基建**：docker-compose backend-common 加 ../jobs:/app/jobs bind mount（镜像COPY jobs会遮旧版, 重启还原——已根治）。

## 2. 生产落地架构（2026-09-02 当前）
- **股票任务账户 stock**：药明康德 SH603259 100股@158.742（2026-08-28建仓补录），现金 234,125.8。
- **t_monitor**（30s轮询，只监控 stock 账户 + 只跑狼大T表达式）——**5 条持续腿**（非消费式，5分钟触发冷却防刷）：
  249 正T低吸(指数整日回撤2-3%) / 250 分时T出(t_sell) / 252 黄线离场(vwap_break) / 253 大盘5min分时急杀≥0.4%(m5_dump) / 254 个股触前日低点+缩量(dip_prev_low & vol_ratio≤0.7)。买腿(249/253/254)带黄线在上护栏(quote.current>quote.average)；卖腿(250/252)一次清T仓(volume=sellable-100，底仓100不动)；T仓=持仓-100。
- **网关白名单**：EXEC_ALLOWED_ACCOUNTS=stock（t 账户下单拒绝；T_EXEC_ALLOWED_ACCOUNTS 可临时放开）。trades.py 做T标的卖出保留100底仓(代码硬拦，防 Pi 卖光底仓)。
- **auto_trade 已恢复**：5 个定时任务 enabled(09-02 下午)；Pi 交易受做T底仓保护+prompt浪型主基调约束。
- **已屏蔽**：vrebounce/vreb_etf/mom_etf/t_build 服务、止损/加仓/建仓/长期池监控（止损只读动态、加仓/建仓监控停用后 Pi 按 6.0 清单主动评估）、旧 t 账户条件12条。
- **数据通道**：brze 代理(tu.brze.top) stk_mins 个股+指数 5min/1min 历史全通（官方 token 无分钟权限、datahubco 限频1次/小时、promax 仅15/30min）；腾讯 qt 实时(含 VWAP 均价)；腾讯/新浪 m5。个股5min已拉：603259/603678/000725/002384/688072 各197天。

## 3. 大周期任务清单（剩余）
按优先级：
- P1 主线三信号融合 ✅ 已实现81%(IS 22/27) + **样本外验证通过(2026-09-02)**：OOS 5/7=71%>代理60%，conc为稳定主力信号，v1权重(0,0.3,0.2,0.5)精度95%维持生产。剩余：科技子类粒度(AI硬vs半导体合并) + 机器人/互金主题覆盖。见 docs/mainline-oos-validation-report.md。
- P1 做T扩样本 ✅ 已重构为三档(09-02)：语料核实"带下来"=个股被拖累盘中低点(非上证整日-2%，年10次太少)，新增 253 大盘分时急杀(0.4%/0.5%, 回测+0.82%/+1.98%) + 254 个股触前日低点+缩量(133天+0.78%)。剩余：盘中口径验证 + 更多个股/历史扩样本。
- P2 主线剩余：科技子类粒度(AI硬vs半导体合并) + 机器人/互金主题覆盖。
- P2 选股链路调度 ✅ 已补 tasks.yaml 自动化(09-02)：根因=08-31 服务器曾含 main_line_judge 调度任务(执行日志 logs/main_line_judge/675cbcdb.json 为证)，09-02 本地20任务配置同步覆盖服务器丢失该条目；09-01/09-02 的 wave/position 状态刷新均为手动运行。已补 main_line_judge(周一8:00) + wave_judge(8:10) + position_judge(8:20，链式 position_class→low_logic_agent→stock_confirm_judge) 三个定时任务；本地+服务器 config 已同步，worker 重启后 23 任务加载验证通过。
- P2 主线内轮动/产业链形态：主线上中下游/软硬切换/去弱留强；**已补(2026-09-02)：主线内"细分宇宙+拥挤度过滤"落地子任务**——国算/液冷/材料/存储 vs 大芯大光(语料核实"国算/液冷正常做T积累成本、材料大级别买点再开")，含**"业绩月后机构调仓日历(R7)+wave转side/build+confirm_chain确认"买点层**。语料/逻辑/case/验证文档：docs/p2-rotation-wolf-logic.md、p2-rotation-cases.md、p2-rotation-validation-analysis.md（wave 17/23，重试补跑中）。
- P2 风控：回避公募重仓+个股大利空(业绩雷/查杠杆/监管)；**新增子项(2026-09-02)：系统性风险联动开关**——银行双头+科技不反 / 龙头·大光破位大黑K 的盘口级告警（复用 confirm_chain F2 证伪、structure_of 双头M顶、腾讯实时行情）。
- P2 宏观/机构行为：两融杠杆/30年国债/美债/汇率/北向/政策。
- P3 持仓纪律：仓位管理/波段头尾做T/不追高不杀跌；**新增子项(2026-09-02)："三仓档位模型"(底仓/T仓/现金 × 浪型档位)**——build=建主线底仓、t_only=只回补已有底仓+做T、side=可埋伏 rel-low、defense=不建；解决"狼大6-7成仓位结构"与"没底仓没资格做T"的闭环缺口。
- P3 复盘认知：日复盘/迭代完善策略。
- P3 数据收尾：补历史新闻/研报/扩样本/README。

## 4. 当前状态（2026-09-02）
- 主线✅ / 浪型v6✅(75%/83%) / 高低位✅(v2 79%) / 确定性门槛✅ / **做T体系✅(T出91% + 正T验证通过 + 黄线离场)**。
- 生产：stock账户药明康德底仓100股，做T 5条持续腿（249/250/252/253/254），auto_trade 5任务已恢复，做T底仓保护+黄线护栏+非消费式腿在跑；worker 仅 TMonitor+t-backtest+动态止损监控(只读)。
- 待观察：正T三档触发质量（249 月1次 / 253 月2-5次 / 254 月20+次），250 分时T出实盘命中；2-4周观察窗口。
- **关键结论(语料实证)**：①狼大"大盘带下来"做正T=**持仓个股被拖累的盘中低点**(每天级)，上证整日-2%只是极端档(年10次)——载体/频率/幅度三重认知修正，见 docs/zt-zhengT-semantics-report.md；②缩转放=转折点信号、方向由位置决定、载体=指数/板块量能(个股5min无预测力)；③分时放量过前高≠加仓点(狼大突破=放量+日线级站稳3天确认, 缩量突破/高位突破是诱多陷阱)——255方案已收回；④T出形态已排除放量突破(二次高点<前高×1.005)，卖飞=两吃不追回(狼大8-12/8-06)；⑤黄线在上才做T(6-30/7-31语料)。

## 5. 关键文档/产物索引
- docs/zt-dip-verification-report.md（正T买点验证通过，含5股扩展）
- docs/t1-stock-verification-report.md（T1个股验证无预测力）
- docs/t1-guard-reback-report.md（指数5min复测：午休伪信号）
- docs/t-monitor-integration.md / t-trading-logic.md（做T接入与狼大逻辑）
- docs/mainline-replay-report.md / wolf-consistency-v2-report.md / **mainline-oos-validation-report.md**（主线重放/一致性/**样本外验证**）
- data/stock_5min_{603259,603678,000725,002384,688072}.json（个股5min, 197天）
- apps/main_line/backtest_zt_dip_v2.py / backtest_t1_stock.py / backtest_t1_paramsweep.py（回测）
- scripts/fetch_stock_5min_brze.py（brze 拉取, 断点续拉）
- **docs/zt-zhengT-semantics-report.md（正T真实语义语料报告）**：'带下来'载体/频率/幅度 + A/B/C信号候选
- docs/mainline-oos-validation-report.md（主线样本外验证 OOS 5/7=71%）
- apps/main_line/backtest_zt_signal_compare.py（正T A/B/C 信号对比回测）
- apps/main_line/backtest_zt_dip_v2.py（原249口径回测）
- 交易执行层改造：backend/app/services/{trade_graph,t_monitor,t_expr,t_db}.py + backend/app/api/{trades,indicator}.py + backend/app/db/prompt_seeds.py + jobs/{market_scan,morning_diagnosis}.py + docker/docker-compose.yml(jobs bind mount)
