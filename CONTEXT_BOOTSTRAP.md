# 新上下文启动包（读狼大文档/帖子 指引）

> 用途：新会话/新上下文接手「狼大交易策略复制」时，按此恢复上下文 + 定向挖狼大语料，避免全量重读。
> 生成：2026-09-01，更新：2026-09-02。

## 0. 先读这几份（恢复上下文，几分钟）
1. 仓库根 /home/fengx/marcus-platform/WOLF_TASKS_OVERVIEW.md —— 大周期/小周期/已完成任务总览（含 09-02 做T体系落地）。
2. 仓库根或服务器 /opt/marcus-platform/data/wave_config.md —— 浪型 v6 冻结配置。
3. 项目持久记忆 dsh-memoir(memoir_read) —— 关键结论/教训/待办。
4. **生产状态（做T, 09-02）**：stock 账户药明康德100股@158.742；3条件(249正T低吸 index.intraday_dd∈[2,3) / 250分时T出 t_sell / 252黄线跌破 vwap_break)；网关白名单 EXEC_ALLOWED_ACCOUNTS=stock；止损监控只读动态离场距离；T仓100/底仓100分离。

## 1. 数据源路径
- 狼大语料：/home/fengx/marcus-platform/狼大回复汇总 20260814-1457&往期.xlsx（sheets：小时代狼大楼最新一周/历史存档、小时代鱼大楼、2026、2025、2022、2020、2016-2017，每行 发帖时间+回复内容）。
- NGA 主帖：tid=47288722（狼大 uid=150058）。用 nga_read_post：scope=op(只看楼主)。
- **分钟数据：brze 代理(tu.brze.top) stk_mins 个股+指数 1/5/15/30/60min 历史全通**（fetch_brze_stk_mins, ≥1s间隔串行）；腾讯 qt 实时(含 VWAP 均价 average)；腾讯/新浪 m5；datahubco 限频1次/小时；官方 token 无分钟权限。已拉 data/stock_5min_*.json（603259/603678/000725/002384/688072, 197天）与 data/index_5min_dh.json（184天）。

## 2. 已完成模块（不用重读，结论在文档/记忆）
- 主线判定（研报重放56%≈资金60%, 需三信号融合）
- 浪型级别判定 v6（方向75%/级别族83%, gate硬拦）
- 高低位分类 v2（一致性 75%→79%, 真实水平）
- 确定性门槛 confirm_chain（确认组 hit 0.86-0.94 vs 硬抄底 0.80）
- **做T体系（09-02）**：分时T出91%验证 + 正T买点(大盘回撤2-3%低吸, T+1 +3.15% hit0.75 验证通过) + 黄线跌破离场；T1缩转放验证无预测力(语义=转折点,位置决定方向,载体=指数/板块量能)。

## 3. 各模块「定向挖语料」关键词清单
用 grep/词频在 xlsx 上按关键词抽狼大原话，作为模块 design/规则依据。

- 确定性门槛：买在确定 | 企稳 | 止跌 | 缩量 | 放量突破 | 突破颈线 | 站稳 | 均线 | 红三兵 | 右侧
- 做T体系：做T | 正T | 反T | 底仓 | T仓 | 缩转放 | 黄线 | 白线 | 分时 | 差价 | 高抛低吸 | 大盘带下来 | 冲高 | 无量 | 前高
- 主线轮动：轮动 | 高低切 | 主线 | 切换 | 上中下游 | 软硬 | 去弱留强 | 龙头 | 兑现
- 风控：公募重仓 | 大利空 | 业绩雷 | 查杠杆 | 两融 | 监管 | 暴雷 | 止损 | 回撤 | 安全第一
- 宏观/机构：宏观 | 美债 | 美元 | 北向 | 机构 | 游资 | 资金流 | GJD | 政策 | 30年国债
- 持仓纪律：仓位 | 满仓 | 减仓 | 加仓 | 不追高 | 不杀跌 | 波段 | 长线 | 止盈

## 4. 建议
- 新上下文接手某模块：先读第0节 → 用第3节关键词在 xlsx 定向挖 10-20 条原话 → 提炼规则 → 落 code + 回测。
- 需要更细实盘语境时，用 nga_read_post 定向取楼主楼层(scope=op + 日期段)。

## 5. 一致性测试（狼大标注集）
- 标注集：data/wolf_labels.json(v1, 34条) / data/wolf_labels_v2.json(v2, 双子代理标注+人工裁决, 111条)。
- 匹配器：apps/main_line/wolf_match.py [标注文件.json]。候选挖掘 mine_full.py → wolf_candidates.json(142条)。
- 一致性：高低位 50%→83%(G1结构)→94%(dip_buy语义)→100%(G2, v1) ；**v2 真实水平 主线60%/高低位75%→79%**（v1 100% 过拟合）。
- 狼大语义要点：高位/低位=结构(双头/M顶)+相对主线涨幅，非绝对价格；低吸3类(强势回踩/超跌埋伏/预告等待)。

## 6. 生产系统注意事项（09-02）
- **只有狼大做T可以操作**：t_monitor 只监控 stock 账户+只跑狼大T表达式(WOLF_T_FIELDS=t_sell/index.intraday_dd/quote.vwap_break)；t 账户下单被网关白名单拒绝。
- 改条件注意 upsert 四键冲突(account_id,symbol,trigger_kind,trade_date) 会覆盖同键旧条件——新语义用不同 trigger_kind(如 custom)。
- 止损监控 STOP_LOSS_DYNAMIC_ONLY=1 只读（黄线距离+前高距离展示），不自动卖；旧8条止损距离体系已屏蔽(代码注释保留)。
- 卖腿始终保留100底仓(狼大『底仓不卖』铁律)；T仓=持仓-100。
- 服务器：81.70.44.68 (marcus) /opt/marcus-platform；worker容器 marcus-worker（bind mount 立即生效，改后重启容器）；postgres marcus-postgres(marcus_trading)。
