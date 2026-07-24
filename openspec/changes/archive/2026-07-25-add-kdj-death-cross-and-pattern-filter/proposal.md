## Why

比亚迪 7/21 建仓当日，日线图上同时出现了 KDJ 高位死叉（K=81.60 < D=82.06，均在 80 以上）、射击之星（上影线/实体 = 6.76x）和量价背离（价格创 96.80 新高但成交量未超越前高峰值），三个信号均指向短期顶部，但现有入场过滤系统全部未检测到——KDJ 死叉从未被检查，K 线形态和量价背离也完全缺失。该笔交易次日跳空低开，亏损 -4.31% 止损出局。

## What Changes

- **Layer 1 新增 KDJ 高位死叉检测**：计算当日及前一日 KDJ-K 与 KDJ-D 值，判断是否发生 K 线下穿 D 线，且发生在 80 以上高位时触发降级（仅试探仓或禁止入场）
- **Layer 3 新增 K 线形态识别**：检测射击之星、看跌吞没两种顶部反转形态，基于日线开/高/低/收四价计算，命中任一形态即触发硬禁止
- **Layer 3 新增量价背离检测**：比较当前高点与前 N 日高点的成交量变化，价格创新高但量能未同步创新高时触发警告（仅试探仓 + 降仓系数 ≤ 0.5）

## Capabilities

### New Capabilities
- `entry-filter-kdj-death-cross`: Layer 1 新增 KDJ 高位死叉检测，K 线下穿 D 线且发生在大於 80 的高位时触发降级
- `entry-filter-pattern-divergence`: Layer 3 新增 K 线顶部反转形态识别（射击之星、看跌吞没）和量价背离检测

### Modified Capabilities
- `entry-filter-overbought`: Layer 3 扩展为"超买+形态+量价"三合一过滤，K 线形态和量价背离作为额外的硬禁止/降仓判定维度

## Impact

- `backend/app/api/indicator.py`: `check_entry_filters` 函数 — Layer 1 新增 KDJ 死叉逻辑（约 40 行），Layer 3 新增 `_eval_pattern_divergence()` 函数（约 80 行）
- `backend/app/models/indicator.py`: `EntryCheckTechDetail` 可能新增 `kdj_k`/`kdj_d` 字段，`LayerResult` 可能新增形态/背离详情
- `backend/app/api/backtest.py`: 回测沙盒同步更新 Layer 1/Layer 3 逻辑
- `servers/pi-server/src/tools.ts`: 格式化输出兼容新增字段
