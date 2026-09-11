# 主升浪五维评价选股系统（复刻「短线炒股分析员」）

## 背景

把外部「短线炒股分析员」AI 的个股评价逻辑复刻进 Marcus：对任意 A 股生成
「主升浪技术画像 + 五维判定 + 量价背离 + 资金分单 + 基本面排雷 + 阶段结论 +
风险定级 + 操作建议 + 观察信号」的结构化报告，并可对当日涨停池批量评分排序选股。

## 组件

| 组件 | 路径 | 说明 |
|------|------|------|
| 核心引擎 | core/main_wave_analyzer.py | 数据获取（Tushare/东财公告/akshare）+ 判定逻辑 + Markdown + 评分 |
| CLI | jobs/main_wave_analyzer.py | 命令行：单票报告 / 对比 / 涨停池选股 |
| API | backend/app/api/main_wave.py | /api/v1/analyze/main-wave/* 三个端点 |
| 调度任务 | config/tasks.yaml → main_wave_scan | 每交易日 15:10 扫描涨停池前 8 只 |
| Agent 技能 | .agents/skills/main-wave-analyzer/SKILL.md | 供 DSH/QQ 智能体调用 |
| 测试 | backend/tests/test_main_wave_analyzer.py | 三只样本票回归验证 |

## 使用

```bash
# 单票报告（Markdown）
.venv/bin/python jobs/main_wave_analyzer.py 600613 --as-of 20260820

# 多票对比
.venv/bin/python jobs/main_wave_analyzer.py 600613 002412 002081 --compare --as-of 20260820

# 涨停池选股 Top10
.venv/bin/python jobs/main_wave_analyzer.py --candidates zt --limit 10

# JSON 输出
.venv/bin/python jobs/main_wave_analyzer.py 002412 --json
```

## 验证结果（2026-08-20 收盘，与人工分析对齐）

| 股票 | 阶段 | 风险 | 建议 | RSI6 | 底部涨幅 | 关键验证点 |
|------|------|------|------|------|----------|------------|
| 神奇制药 600613 | 末期加速赶顶 | 极高 | 不碰/不追高 | 100 | +83.7% | 5 个主升浪涨停、8/19 天量假阴、张之君减持、3 次异动公告 |
| 汉森制药 002412 | 加速初期 | 中 | 一字板买不进，等开板观察 | 99 | +57.8% | 7/24 高开暴跌洗盘、蓄力 17 天、1 个缺口 8.42-9.26、PE 20.68 |
| 金螳螂 002081 | 已结束，进入回调 | 极高 | 不碰；反弹离场 | 72.5 | +66.7% | 8/18 天量见顶 + 8/19 跌停、高点回撤 -19.8%、趋势转横盘 |

说明：资金分单（BBD/大单差/DDX）为 tushare moneyflow 口径近似值，与东财盘口的
绝对值不同但方向一致（超大单拉板 / 中大单出逃 / 散户接盘的判别逻辑相同）。

## 全市场批量扫描（--candidates market）

按交易日全量拉取 daily（N 天）+ 单日 daily_basic/moneyflow/limit_list_d/stock_basic，
本地向量化评分全部股票（5206 只仅需 ~2s），再对 TopN 做深度分析。

实测耗时（2026-08-20 两轮实跑，Tushare 限流约 10 次/分钟）：

| 阶段 | 初版（深度重复拉数） | 优化后（缓存复用） |
|------|---------------------|-------------------|
| trade_cal | 0.9s | 0.2s |
| daily 全量拉取 | 611s（**瓶颈**） | 245s（无并发扫描时 ~4.1s/天） |
| 快照（basic+moneyflow+limit+info） | 11.3s | 8.2s |
| 本地评分 5206 只 | 2.3s | 2.2s |
| TopN 深度分析 | 58s（且被限流打挂） | 14.4s（预填缓存） |
| **合计** | **~11.4 分钟** | **~4.5 分钟** |

结论：90 天历史全市场（5206 只）批量扫描 **4.5~11 分钟**，其中 daily 拉取占 90%；
60 天约 3-8 分钟，30 天约 2-5 分钟；本地评分本身只要 ~2 秒。

用法：`python jobs/main_wave_analyzer.py --candidates market --limit 20 --as-of 20260820`

优化方向：60/30 天历史把拉取时间砍半/砍到 1/3；tushare 更高积分可放宽频控；
或接 akshare/东财按日全量接口并行拉取。

## 判定规则摘要

见 .agents/skills/main-wave-analyzer/SKILL.md 的「判定规则速查」。