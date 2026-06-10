---
name: weekly-reflection-iteration-plan
overview: 基于周度反思报告暴露的4个核心问题，在Pi prompt层和scheduler代码层实施改进：立场联动熔断、盘前继承复盘、建仓硬门槛正式化、Pi立场灵敏度提升。
todos:
  - id: 1-prompt-divergence
    content: 修改 index.ts TRADE_SYSTEM_PROMPT：第一步增加立场偏离检测规则，第二步增加"建仓硬门槛"节（价格确认连续2次通过、候选池>=3只A/B级、首仓
    status: completed
---

## 产品概述

基于本周 Marcus 周度反思报告暴露的四个核心问题，实施三项迭代优化，使 Pi 系统在立场判定上从"惯性滞后"升级为"实时响应"。

## 核心功能

### 1. 扫描-Pi 立场联动熔断

当盘中扫描报告的立场（如 hold/red）与 Pi 当前立场（如 yellow/60%）出现显著偏离时，在交易 prompt 中注入警告，强制 Pi 重新评估立场。熔断条件：扫描立场与 Pi 立场偏离 >= 2 档，或连续 3 轮资金流出扩大。

### 2. 盘前继承前日复盘信号

盘前 Pi 分析（_call_pi_analysis 的盘前分支）自动读取前一日最终的 Pi 分析结果（取自 memory/pi-analysis-logs/ 的最新记录），将其立场和仓位上限作为盘前分析的基准约束。若前日最终复盘为 red 或催化强度 < 20，盘前立场不得超出 yellow/40%。

### 3. 正式化首仓上限规则

将 Marcus 执行层实际应用的"隐性保守折扣"写入 TRADE_SYSTEM_PROMPT 成为明文规则：**首仓不超过建议仓位的 50%**（Pi 说 60% → 首仓 ≤ 30%）。价格确认和候选池门槛暂不加入，观察一周后再评估是否需要。

## 技术栈

- Python 3.11（scheduler_service.py）
- TypeScript（pi-server/src/index.ts，字符串模板中的 System Prompt）
- JSONL 文件读写（memory/pi-analysis-logs/）

## 实现方法

### 策略一：代码层立场偏离检测 + Prompt 注入（解决滞后问题）

在 `_execute_pi_trade()` 中，构造交易 prompt 前增加一个预检步骤：

1. 读取最新扫描报告的 stance 和 position_limit（复用 `_get_latest_pi_analysis` 逻辑）
2. 获取当前策略链中上一轮 Pi 的 stance 和 position_limit
3. 计算偏离度：若扫描立场比 Pi 立场保守两档以上（如 Pi=yellow, 扫描=hold/red），或扫描 position_limit < Pi position_limit 的 50%，生成立场偏离警告
4. 将该警告注入 prompt 正文，位于交易指令之前

同时在 TRADE_SYSTEM_PROMPT 中增加"立场偏离检测"规则，使 Pi 调用 get_latest_scan_report 后主动对比 scan stance vs pi_analysis stance，发现偏离时优先以降级处理。

### 策略二：盘前加载前日复盘（解决信号断链问题）

在 `_call_pi_analysis()` 的盘前分支中：

1. 新增 `_get_recent_pi_review()` 辅助方法，读取 `memory/pi-analysis-logs/` 中前一日的最新 Pi 分析记录
2. 提取其中的 stance、position_limit、reason 字段
3. 若前日最终 stance 为 red 或 position_limit <= 20%，或 reason 中包含"催化强度 < 20"等降级信号，将其作为前置条件注入盘前分析 prompt
4. Prompt 中明确要求："若上调仓位需额外确认，盘前立场不得超过前日复盘仓位的 2 倍"

### 策略三：正式化首仓上限规则（Prompt 层规则增强）

在 TRADE_SYSTEM_PROMPT 的"第四步：仓位计算"开头新增"**首仓上限**"规则：

- **首仓不超过建议仓位的 50%**（Pi 说 60% → 首仓 ≤ 30%；Pi 说 40% → 首仓 ≤ 20%）
- 第二次建仓需前一仓已有浮盈（避免越跌越补）

> ⏸️ 价格确认和候选池门槛暂不加入，观察一周后再评估。

此规则与已有的"总仓位 ≤ 60%"、"单票 ≤ 15%"形成互补，不冲突。

## 实现注意事项

### 向后兼容

- 立场偏离检测仅生成警告并注入 prompt，不跳过交易流程
- 盘前复盘加载在复盘数据缺失时回退到默认 yellow/60%
- 建仓硬门槛仅适用于买入，不影响卖出/止损逻辑
- 不修改 scan.py 的 stance 判定逻辑

### 性能

- 立场偏离检测只做两次 JSONL 文件读取（当日扫描 + 前日复盘），IO 开销 < 10ms
- 不引入新的 API 调用

### 日志

- 立场偏离警告注入时记录：`[stance_divergence] scan_stance=X, pi_stance=Y, warning_injected=True`
- 盘前复盘加载时记录：`[pre_market] loading prev review: stance=X, limit=Y%`

### 风险控制

- `_get_recent_pi_review()` 在文件不存在时返回 None，调用方回退到默认值
- 所有变更限定在 scheduler_service.py 和 index.ts 两个文件
- 不改变 strategy_chain 数据结构

# Agent Extensions

<extension>
<name>code-explorer</name>
<purpose>在制定修改方案时已深度探索了 index.ts 的 TRADE_SYSTEM_PROMPT（立场判定规则、SOP 流程）和 scheduler_service.py 的 _execute_pi_trade、_call_pi_analysis 方法的完整代码，确认了修改位置和现有数据流。</purpose>
<expected_outcome>已确认所有修改位置精确可行，无接口冲突。</expected_outcome>
</extension>