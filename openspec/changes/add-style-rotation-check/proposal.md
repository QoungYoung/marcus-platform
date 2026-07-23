## Why

当前市场诊断仪表盘只能判断"趋势市 vs 震荡市"，无法识别市场内部的风格偏好方向。实战中经常出现：趋势市但资金全部涌入银行/红利（防御风格），或震荡市但科技板块持续跑赢（进攻风格）。缺少风格方向的诊断，导致仓位策略无法做到"防御模式下科技只卖不买"这类精细控制。

## What Changes

- **新增第6项诊断指标「风格轮动检查」**：在 `/market/market-diagnosis` 端点内新增 `style_rotation` 指标，对比防御/科技/资源三个风格篮子的相对强弱
- **价格维度（近5日）**：用 `index_classify` 动态获取申万行业代码体系，按名称映射到防御/科技/资源三个篮子，通过 `index_daily` 对比篮子平均涨跌幅
- **资金维度（近10日）**：用东财概念板块主力净流入数据，按关键词聚合到三个风格篮子
- **三状态风格判定**：进攻(OFFENSE) / 防御(DEFENSE) / 资源避险(RESOURCE_HEDGE) / 均衡(NEUTRAL)，两维度双确认+连续3天跑赢触发
- **QQ推送展示**：`morning_diagnosis.py` 格式化输出风格结论
- **AI提示词注入**：`trade_graph.py` 读取风格信号，注入交易决策上下文
- **仓位策略联动**：`indicator.py` 根据风格模式动态调整科技/防御板块的仓位上限

## Capabilities

### New Capabilities
- `style-rotation-check`: 风格轮动检测系统，对比防御/科技/资源三个篮子的价格强弱和资金流向，输出进攻/防御/资源避险/均衡四种风格模式

### Modified Capabilities
- `market`: 市场诊断端点新增 `style_rotation` 指标字段，DB 表 `indicators_json` 扩展包含风格数据

## Impact

- `backend/app/api/market.py`: 新增 `_compute_style_rotation()` 函数 + 扩展 `_SW_SECTOR_CODES` + 在 `get_market_diagnosis()` 中调用
- `backend/app/services/trade_graph.py`: `_read_market_regime()` 扩展，读取风格信号注入 AI 提示词
- `backend/app/api/indicator.py`: `_get_market_regime_for_calc()` 扩展，按风格调整板块仓位上限
- `jobs/morning_diagnosis.py`: `format_message()` 新增风格轮动展示区块
- DB: `market_diagnosis.indicators_json` 自然扩展（Text字段，无需DDL）
