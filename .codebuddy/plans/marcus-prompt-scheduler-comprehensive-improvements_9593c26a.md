---
name: marcus-prompt-scheduler-comprehensive-improvements
overview: 基于 6/16 稀土永磁产业链建仓事件的深度复盘，对 Marcus 交易系统进行三层面改进：Prompt 精简优化（~-16%行数）、调度器增加 9:50 跟进窗口（15分钟产业链三级建仓）、API 层新增 7 个预计算指标（RSR/资金效率/安全垫/信号强度/日内分位/集中度/弱势排名）。
todos:
  - id: prompt-refactor
    content: 重构 prompt_seeds.py 的 TRADE_SYSTEM_PROMPT：MA 过滤强制切换为 get_realtime_indicators 盘中实时 MA、删除涨停次龙头规则、合并 5 层筛选与产业链构建流程、新增产业链建仓计划与最低覆盖要求、精简删除重复章节（交易理念/风格/框架/T+1 详解/操作纪律/沟通风格/概念映射）、更新工具表和时段描述
    status: completed
  - id: scheduler-add-950-window
    content: 在 tasks.yaml 新增 auto_trade_mid_morning 9:50 定时任务，同步修改 scheduler_service.py 的 TASK_TIME_WINDOWS、TRADE_DAY_ONLY_TASKS 和 _execute_pi_trade 方法增加 mid_morning 时段分支
    status: completed
  - id: api-add-p0-indicators
    content: 在 market.py/api 中为 get_quote 增加 rsr 和 intraday_percentile 字段，为 get_moneyflow 增加 capital_efficiency 字段，为 get_concept_fund_flow 增加 signal_level 字段，在 indicator.py 新增 /indicator/safety-margin/{symbol} 安全垫检查端点，同步更新 models 中的 Pydantic 模型定义
    status: completed
  - id: api-add-p1-indicators
    content: 在 portfolio.py 的 get_portfolio 函数中增加 sector_concentration（按行业+相关性双维度）和 sector_rank/sector_rank_pct（持仓弱势排名）的计算逻辑，更新 PortfolioSummary 模型
    status: completed
    dependencies:
      - api-add-p0-indicators
---

## 产品概述

针对 6/16 稀土永磁产业链建仓事件暴露的三大系统性缺陷，对 Marcus 短线右侧交易系统进行全面改进。改进后系统将具备：盘中实时 MA 过滤替代盘后静态 MA、产业链"先规划后执行"建仓流程、15 分钟间隔的三段式早盘建仓节奏、以及 7 项代码层预计算指标供 AI 直接参考。

## 核心功能

### Prompt 精简重构（TRADE_SYSTEM_PROMPT）

- **MA 过滤数据源强制切换**：建仓过滤改用 `get_realtime_indicators` 的盘中实时 MA5/MA20，废弃 `get_technical` 的盘后静态 MA
- **删除涨停次龙头规则**：龙头涨停后不再追次龙头，改为"该产业链当日放弃新建仓位"
- **产业链建仓计划**：建仓前先规划全部 3 个环节的建仓计划表，再按顺序执行，当日至少覆盖 2 个环节
- **合并重复内容**：5 层筛选与产业链构建流程合并、交易理念/风格/框架合并到角色定义、删除重复的 T+1 规则和操作纪律章节
- **工具表更新**：增加 `get_realtime_indicators` 工具说明

### 调度器新增 9:50 建仓窗口

- 新增 `auto_trade_mid_morning` 定时任务（9:50 触发）
- 实现 15 分钟间隔三段式节奏：9:35 上游龙头建仓 → 9:50 中游建仓 → 10:05 下游建仓（沿用现有 10:35 窗口做趋势确认）
- 配合同步更新 scheduler_service.py 中的时间窗口校验、交易日检查、时段模式指令

### API 层新增 7 项预计算指标

- **RSR 相对强弱比**：个股涨幅/板块涨幅，精确量化是否跑输板块
- **资金效率指数**：净流入占比/涨幅占比，判断涨幅是否有主力资金背书
- **建仓前安全垫检查**：止损距离/日内剩余波动风险，评级安全/偏紧/危险
- **信号强度标签**：基于板块资金+涨停数量的 ⚡极端/🔥偏强/📊常规 分级
- **日内价格分位**：判断是否追在日内高点（>90%分位即高风险）
- **板块集中度**：单一行业+高相关组合总暴露占比
- **持仓弱势排名**：持有个股在同板块中的涨幅排名百分比

## 技术栈

- **后端框架**：Python + FastAPI（项目现有）
- **任务调度**：APScheduler + YAML 配置（项目现有）
- **数据模型**：Pydantic BaseModel（项目现有）
- **数据源**：Tushare / 同花顺 / 东方财富 / 雪球（项目现有）

## 实现方案

### 方案概览

所有改进均在现有代码基础上进行重构，不引入新的架构模式或外部依赖。按三个独立层面并行推进：Prompt 层是纯文本重构不涉及逻辑变更，调度器层是配置+少量代码分支追加，API 层是模型字段扩展+端点新增。

### 层面一：Prompt 重构策略

**重构原则**：8 字核心 —— "按强度分级，用实时数据"。不在原规则上层层叠加例外条款，而是更换数据源（盘后 MA → 盘中实时 MA）和删除逻辑悖论规则（涨停次龙头）。

**三段式建仓节奏**：

```
9:35 auto_trade_morning     → 产业链建仓计划表 + 上游龙头建仓
9:50 auto_trade_mid_morning → 中游建仓跟进（新窗口）
10:35 auto_trade_late_morning → 趋势确认 + 止损/加仓
13:35 auto_trade_afternoon  → 午后调整
14:30 auto_trade_closing     → 只卖不买
```

**合并后的核心 SOP 流程**（从当前 62 行精简到约 30 行）：

1. 主线确认：资金 TOP5 ∩ 涨幅 TOP5 → 当日主线
2. 产业链规划：概念拆解 → 行业分层 → 纯度验证 → 龙头确认 → 建仓计划表（先规划全部 3 个环节）
3. 技术面检查：逐只使用 get_realtime_indicators 的盘中实时 MA 做过滤
4. 执行下单：按计划表顺序买入，等待期间并行准备下一只
5. 全天锁定：当日所有买入只在主线产业链上展开

### 层面二：调度器改进策略

**tasks.yaml 新增任务**：

```
- id: auto_trade_mid_morning
  name: 自动交易 (早盘中游)
  description: 9:50 产业链中下游跟进建仓
  enabled: true
  type: pi_trade
  pi_prompt: mid_morning
  schedule:
    type: cron
    expr: 50 9 * * *
    timezone: Asia/Shanghai
  depends_on:
  - market_scan
```

**scheduler_service.py 修改**：

- TASK_TIME_WINDOWS 字典新增 `'auto_trade_mid_morning': ('早盘中游', 9*60+25, 11*60+35)`
- TRADE_DAY_ONLY_TASKS 集合新增 `'auto_trade_mid_morning'`
- `_execute_pi_trade` 方法新增 `mid_morning` 分支，指令为"9:50 产业链中下游跟进建仓"

### 层面三：API 指标扩展策略

**数据模型扩展模式**：在现有 Pydantic 模型中增加 Optional 字段，向后兼容，不影响现有调用方。

**七个新指标的计算位置**：

| 指标 | 计算位置 | 新增字段 |
| --- | --- | --- |
| RSR | `get_stock_quote` 函数 | `rsr: Optional[float]` |
| 资金效率 | `get_stock_moneyflow` 函数 | `capital_efficiency: Optional[float]` |
| 安全垫检查 | 新增 `/indicator/safety-margin/{symbol}` | 独立端点 |
| 信号强度 | `get_concept_fund_flow` 函数 | `signal_level: Optional[str]` |
| 日内分位 | `get_stock_quote` 函数 | `intraday_percentile: Optional[float]` |
| 集中度 | `get_portfolio` 函数 | `sector_concentration: Optional[dict]` |
| 弱势排名 | `get_portfolio` 函数 | `sector_rank: Optional[int]`, `sector_rank_pct: Optional[float]` |


**核心计算逻辑**：

- **RSR**：调用 `get_concept_mapping` 找到个股所属概念 → 取概念板块的 `pct_change` → `rsr = 个股percent / 板块pct_change`
- **安全垫检查**：需传入 `symbol` + `entry_price` 或从 get_quote 获取当前价 → `stop_distance = entry_price * max(0.05, ATR*1.5)` → `intraday_risk = ATR * sqrt(remaining_minutes/total_minutes)` → `rating = stop_distance / intraday_risk`
- **信号强度**：板块资金净流入 > 40亿 + ≥2只涨停 → ⚡极端；15-40亿 + ≥1只涨停 → 🔥偏强；其他 → 📊常规
- **弱势排名**：遍历同概念板块所有成分股的当日涨幅，计算持仓标的名次百分比