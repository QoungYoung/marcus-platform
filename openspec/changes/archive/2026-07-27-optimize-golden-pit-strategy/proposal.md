## Why

当前黄金坑系统只管买入不管卖出，退出规则完全缺失。仓位分级参数（3%/15%/50%/75%/100%）和信号阈值（P5/P10）对所有指数一刀切，没有利用回测已揭示的指数间巨大差异（科创50 20日 +15.9% vs 上证50 +1.2%）。现在是系统从"能跑"到"能赚钱"的关键一步。

## What Changes

- **动态退出规则**: 贪婪值回升到 P50 卖一半，P70 全清；拐点后连续2天回落→止盈退出
- **分指数参数化**: 每个指数独立的入场阈值、仓位曲线、拐点确认天数，替代全局常量
- **仓位曲线回测校准**: 用完整入场→退出回测找到每指数最优的 3%/15% 和 50-75-100 参数
- **多指数共振确认**: 入坑指数数量影响仓位系数（3+ 指数 → 1.2x，1 个 → 0.6x）
- **拐点确认天数差异化**: 高弹性指数（科创50）用1天确认，低弹性（沪深300）用2-3天

## Capabilities

### New Capabilities
- `golden-pit-exit`: 基于贪婪值回升的动态退出规则，含止盈和止损
- `golden-pit-per-index-params`: 每个宽基指数独立的信号阈值、仓位曲线、确认参数

### Modified Capabilities
<!-- 本次不修改已有 spec 的行为契约，所有改动都是新增能力 -->

## Impact

- `backend/app/services/golden_pit_service.py`: 新增 `_detect_exit_signal()`、指数参数配置表扩展
- `backend/app/services/golden_pit_dca_service.py`: 退出执行、共振系数、分指数仓位
- `scripts/backtest_golden_pit_v7.py`: 新建，完整入场→退出回测
- `backend/app/api/golden_pit.py`: 新增退出信号端点（供前端展示）
- `frontend/src/pages/GoldenPitPage.tsx`: 展示退出建议和共振状态
