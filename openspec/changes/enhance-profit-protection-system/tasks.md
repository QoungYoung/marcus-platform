## 1. P2 数据层：超买指标数据获取

- [ ] 1.1 在 `_tech_divergence.py` 中新增 `get_overbought_indicators(symbol, cache)` 函数，复用现有 Tushare stk_factor_pro + 腾讯实时行情链路，返回 `(kdj_k, rsi6, daily_change_pct)` 三元组
- [ ] 1.2 实现实时估算优先、历史确认降级的 KDJ_K/RSI6 取值逻辑：腾讯实时行情可用时调用 `calculate_realtime_indicators()` 获取盘中估算值，不可用时降级为 Tushare stk_factor_pro 昨日确认值
- [ ] 1.3 实现 60 秒独立缓存，与 5 信号评估的 1 小时缓存分离

## 2. P1 基础设施：会话峰值浮盈追踪

- [ ] 2.1 在 `StopLossMonitor.__init__` 中新增 `_session_max_float: Dict[str, float]` 字典
- [ ] 2.2 在 `_daily_reset()` 中添加 `_session_max_float.clear()`
- [ ] 2.3 在 `_check_all_positions()` 的持仓循环中，每次轮询后更新 `_session_max_float[symbol] = max(current, stored)`

## 3. P1 规则逻辑：Iron Rule 2 改用峰值判定

- [ ] 3.1 在 `_check_iron_rule2` 开头获取会话峰值浮盈：`max_float = self._session_max_float.get(symbol, float_pnl_pct)`
- [ ] 3.2 将四档判定（T3/T2/T1.5/T1）的输入从 `float_pnl_pct` 改为 `max_float`
- [ ] 3.3 保持触发条件不变（仍然用当前 `float_pnl_pct < protect_pct` 判断是否穿透保护线）
- [ ] 3.4 保持 HWM 增强（曾大盈 ≥5% 转亏损保本）逻辑不变

## 4. P2 基础设施：超买历史追踪

- [ ] 4.1 在 `StopLossMonitor.__init__` 中新增 `_overbought_history: Dict[str, List[str]]` 字典，value 为 KDJ_K ≥ 80 的日期列表
- [ ] 4.2 在 `_daily_reset()` 中不清空超买历史（需跨日追踪连续天数），改为按需在超过 5 天间隔后自动清理
- [ ] 4.3 新增 `_update_overbought_history(symbol, kdj_k)` 方法：当日 KDJ_K ≥ 80 时追加今日日期到列表，KDJ_K < 80 时检查并可能重置连续计数

## 5. P2 规则逻辑：超买止盈三级判定

- [ ] 5.1 新增 `_check_overbought_take_profit(symbol, float_pnl_pct)` 方法，按三档递进判定
- [ ] 5.2 实现第三档（最高优先级）：连续 3 日 KDJ_K ≥ 80 → 返回 `(reason, 1.0)` 强制清仓
- [ ] 5.3 实现第二档：KDJ_K ≥ 80 + RSI6 ≥ 75 + 单日涨幅 > 3% → 返回 `(reason, 0.5)` 减仓 50%
- [ ] 5.4 实现第一档（最低优先级）：KDJ_K ≥ 80 首次触发 → 返回 `(reason, 0.3)` 减仓 30%
- [ ] 5.5 同一交易日内第一档只触发一次（用 `_triggered` 字典去重键追踪）

## 6. 规则链集成

- [ ] 6.1 在 `_evaluate_stop_rules` 中，规则 2（铁律二）之后插入规则 2.3（超买止盈）调用
- [ ] 6.2 确保规则 2.3 返回 `(None, 1.0)` 时继续检查后续规则 2.5/2.6/3
- [ ] 6.3 规则 2.3 的去重键格式与现有 `_triggered` 体系兼容

## 7. 验证

- [ ] 7.1 用拓维信息 7/13 行情数据模拟验证 P1：浮盈 +12%→+2%，应触发 Iron Rule 2 在 +5% 保护线
- [ ] 7.2 用紫光股份 7/6-7/13 行情数据模拟验证 P1 + P2：7/10 KDJ=90 应触发超买止盈减仓 30%，7/13 连续第 3 天超买应触发强制清仓
- [ ] 7.3 用光线传媒 7/8-7/10 行情数据模拟验证 P2：7/10 KDJ=81 + RSI=76 + 涨幅 4% 应触发第二档 50% 减仓
- [ ] 7.4 确认 `get_position_stop_distances()` 的 `rule_distances` 返回中增加规则 2.3 的距离信息
