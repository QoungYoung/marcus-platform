## 1. 数据提取层

- [x] 1.1 在 `check_entry_filters` 中提取上一日 KDJ-K/D 确认值（从 `get_realtime_indicators` 返回的 `historical` 字段取 `historical[0]` 的 `kdj_k`/`kdj_d`）
- [x] 1.2 在 `check_entry_filters` 中提取最近 5 根日 K 线（开盘/最高/最低/收盘/成交量），复用已有的 `get_daily_kline_qfq` 调用

## 2. KDJ 高位死叉检测（Layer 1）

- [x] 2.1 新增 `_eval_kdj_death_cross(prev_k: float, prev_d: float, cur_k: float, cur_d: float) -> tuple[LayerResult, float]` 函数
- [x] 2.2 实现交叉判定逻辑：prev_k >= prev_d（昨日金叉或持平）且 cur_k < cur_d（今日死叉）→ 确认交叉
- [x] 2.3 实现分级：cur_k < 70 → Pass，70 ≤ cur_k < 80 → Warning（仅试探仓 multiplier≤0.5），cur_k ≥ 80 → Blocked（禁止入场 multiplier=0）
- [x] 2.4 将 `_eval_kdj_death_cross` 调用插入 Layer 1 过滤流程，结果合并到 `tech_details` 和 `downgrade_multiplier`

## 3. K 线形态 + 量价背离检测（Layer 3）

- [x] 3.1 新增 `_detect_patterns(bars: list) -> dict` 函数，基于最近两根已完成日线实现射击之星和看跌吞没识别
- [x] 3.2 射击之星判定：实体 = |close - open|，上影线 ≥ 2.0×实体，下影线 ≤ 0.5×实体，实体 > 0
- [x] 3.3 看跌吞没判定：bar[-1] 收阳，bar[0] 收阴，bar[0].open > bar[-1].close，bar[0].close < bar[-1].open
- [x] 3.4 新增 `_eval_volume_divergence(bars: list) -> bool` 函数：当日最高价为近 5 日最高 且 当日成交量 < 近 5 日最大成交量 → 背离
- [x] 3.5 将形态/背离结果整合到 `_eval_overbought` 中（形态硬禁止 severity=3，背离警告 severity=1），更新共振逻辑

## 4. 向后兼容与模型更新

- [x] 4.1 `EntryCheckTechDetail` 新增 `kdj_k: float = 0` 和 `kdj_d: float = 0` 字段
- [x] 4.2 `LayerResult.details` 列表追加形态/背离/死叉的检测描述
- [x] 4.3 确保 API 响应顶层字段不变，前端无需改动

## 5. 回测沙盒同步

- [x] 5.1 `backend/app/api/backtest.py` 的 Layer 1/Layer 3 逻辑同步更新：引入 `_eval_kdj_death_cross`，Layer 3 传入 K 线数据供形态/背离检测

## 6. Pi Server 格式化更新

- [x] 6.1 `servers/pi-server/src/tools.ts` 的 `check_entry_filters` 格式化输出兼容新增的 details 条目

## 7. 验证

- [x] 7.1 用比亚迪 7/21 数据验证：KDJ 高位死叉应触发 Layer 1 Blocked，射击之星应触发 Layer 3 Hard Block
- [x] 7.2 用正常股票数据验证：无死叉/无形态/无背离时不应产生误判
