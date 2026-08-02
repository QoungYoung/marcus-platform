## Why

当前方向预测系统存在训练-推理分布漂移、预测目标与实盘收益错配、缺乏动态风控层级等架构缺陷。经过三轮专家评审，识别出目标变量（单日脉冲 vs 持有期收益）的致命错误。本次重构将全部修复点落地为可执行代码，构建一套逻辑自洽、实盘可用的纯多头行业龙头排序系统。

## What Changes

- **BREAKING**: 预测目标从 `P(return > 0)` 二分类改为持有期收益 `(T+N_close - T_open) / T_open` 回归
- **BREAKING**: 全部个股权重特征滞后 1 天（T → T-1），消除未来函数嫌疑
- **BREAKING**: 移除 9 维市场级常数列特征，缩至 26 维纯截面特征
- **BREAKING**: API 输出字段从 `up_probability` / `confidence` 改为 `expected_return`
- 新增 `RiskManager` 服务：基于净值回撤的动态仓位缩放（1.0→0.2，永不下牌桌）
- 新增 `ModelUpdateScheduler`：月度固定重训 + 连续 3 日跑输基准紧急重训触发器
- 资金流实时模式：East Money → 前向填充（TTL=1天）→ 20日中位数兜底，删除"成交额×涨跌幅"代理
- 所有价格使用前复权（adj_factor）调整
- 训练目标：分板块 Winsorize + Pseudo-Huber Loss
- 评估体系重构为纯多头口径：Information Ratio、月胜率、Calmar、最大回撤

## Capabilities

### New Capabilities
- `direction-prediction-v3`: 基于滞后特征 + 持有期收益回归的行业龙头方向预测模型
- `risk-manager`: 实盘组合动态仓位风控，基于净值回撤连续缩放
- `model-update-scheduler`: 月度固定重训 + 紧急触发器调度

### Modified Capabilities
- `trading`: 方向预测 API 输出字段变更（`expected_return` 替代 `up_probability`），新增行业分散约束

## Impact

- Affected code: `backend/app/services/direction_prediction.py`（重写）, `backend/app/api/direction.py`（重写）, `scripts/dump_direction_data.py`（重写）
- New files: `backend/app/services/risk_manager.py`, `backend/app/services/model_update_scheduler.py`
- Dependencies: 已有（xgboost, scikit-learn, pandas, numpy, scipy, optuna），无新增
- Breaking API: `/api/v1/direction/predict` 响应格式变更，`/api/v1/direction/validate` 指标字段变更
