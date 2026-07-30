## 1. 参数层 — CHINA_INDICES 扩展

- [x] 1.1 在 `golden_pit_service.py` 中定义全局默认趋势因子映射 `DEFAULT_TREND_FACTORS`
- [x] 1.2 为每个已启用的指数添加 `trend_factors` 字段（中证1000/恒生用覆盖值，其余用默认）
- [x] 1.3 为每个已启用的指数添加 `dca_fallback` 字段（lump_entry 设 5 天，uniform_3 设 15 天）
- [x] 1.4 新增 `get_trend_factor()` 函数：根据 trend 状态和指数配置返回趋势因子

## 2. 核心逻辑 — DCA 仓位计算重构

- [x] 2.1 重构 `execute_golden_pit_dca()` 仓位计算段落（~L666-L718），替换为 DCA权重 × 趋势因子
- [x] 2.2 接入 `_strategy_weights()` 调用（当前函数存在但未被调用），获取该指数的每日 DCA 权重
- [x] 2.3 实现趋势状态→因子映射：declining=0.1 / bottoming=0.5 / turning=1.0 / accelerating=1.2 / full=1.5
- [x] 2.4 实现趋势因子加速阈值保护：greed > entry_greed 时 factor 上限=1.0
- [x] 2.5 实现完整叠加顺序：`max_total × dca_weight × trend_factor × position_multiplier × resonance × macro_coef`

## 3. 安全制动 — 三层硬约束

- [x] 3.1 实现假信号检测：greed 突破 entry_greed → 暂停该指数买入，标记窗口 `aborted`
- [x] 3.2 实现飞刀保护：单日 greed 跌幅 > 2 个百分点 → 跳过当日买入，不递增 schedule_day
- [x] 3.3 实现累计硬截断：total_invested + daily_amount > max_total → 截断为剩余额度

## 4. 窗口管理 — 进度追踪与兜底

- [x] 4.1 在 `golden_pit_dca_log` 模型中新增 `schedule_day` 和 `trend_factor` 字段
- [x] 4.2 实现窗口进度追踪：信号触发时 schedule_day=0，每次成功执行后 +1
- [x] 4.3 实现 DCA 窗口超时兜底：schedule_day > dca_fallback 时 trend_factor 强制=1.0
- [x] 4.4 实现二次信号检测：greed 较信号触发日创新低（>5%容差）→ 重置 schedule_day=0（最多1次）
- [x] 4.5 实现假信号中止后的窗口清理：窗口标记 aborted 后不再恢复

## 5. 日志与可审计性

- [x] 5.1 DCA log 的 `strategy` 字段改为编码关键决策参数（如 `uniform_3/turning/1.0x`）
- [x] 5.2 安全制动触发时记录 `status=safety_brake`，`strategy` 包含制动类型
- [x] 5.3 `_record_dca_log()` 新增 `schedule_day` 和 `trend_factor` 参数，写入对应字段

## 6. 前端展示

- [x] 6.1 在指数卡片和 DCA 状态表中显示 DCA 策略类型和当前窗口进度
- [x] 6.2 显示趋势状态和当前调节因子（如 "turning ×1.0x" / "declining ×0.1x 减速中"）
- [x] 6.3 API 返回安全制动状态信息（status/strategy 字段），前端展示跳过原因

## 7. 验证

- [x] 7.1 单元验证：手动构造不同 trend 状态，确认 daily_amount 计算符合预期
- [x] 7.2 对比验证：用当前生产参数跑一次 DCA 周期，确认不会低于原有建仓速度
- [x] 7.3 边界验证：模拟 declining 持续 20 天 → dca_fallback 触发 → 强制完成 的完整流程
