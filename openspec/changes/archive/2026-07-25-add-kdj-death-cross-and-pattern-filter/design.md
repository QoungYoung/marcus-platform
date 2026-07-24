## Context

`check_entry_filters` 当前的三层过滤中，Layer 1 仅检查 MACD DIF/DEA 金叉死叉，而 KDJ 的 K/D 交叉完全未被关注；Layer 3 仅检查 RSI6/KDJ-J/CCI 的数值阈值。比亚迪 7/21 案例证明：MACD 仍在金叉时 KDJ 已高位死叉，且日线出现了射击之星+量价背离。这三类信号属于不同的维度（趋势动能 / K 线形态 / 量价关系），不应被忽略。

已有的技术数据源：
- `get_realtime_indicators` → MA/MACD/KDJ/RSI 盘中估算 + 前 3 日盘后确认值 (`historical`)
- `get_technical` → Tushare `stk_factor_pro` 盘后确认日频数据（含 KDJ-K/D/J 的 qfq 版本）
- `get_daily_kline_qfq` → 前复权日K线（开/高/低/收/量）

## Goals / Non-Goals

**Goals:**
- Layer 1 新增 KDJ 高位死叉检测：比较当日与前一日 KDJ-K 与 KDJ-D，K 从上方穿越 D 且发生在 80 以上高位时触发降级
- Layer 3 新增射击之星检测：基于日线 OHLC 四价，实体小 + 上影线 ≥ 2x 实体 + 无显著下影线
- Layer 3 新增看跌吞没检测：前日阳线+当日阴线，当日开盘 > 前日收盘，当日收盘 < 前日开盘
- Layer 3 新增量价背离检测：当日创 N 日新高但成交量 < 前 N 日最大成交量
- 所有新检测向后兼容，不改动 `LayerResult` 的顶层字段结构

**Non-Goals:**
- 不新增其他 K 线形态（晨星、三只乌鸦、锤子线等）——仅覆盖最可靠的两种顶部反转形态
- 不做连续多日的趋势线/通道突破检测
- 不引入新的外部数据源或 API
- 不修改前端 UI

## Decisions

### Decision 1: KDJ 死叉数据来源 → 复用 realtime 的 historical 字段

`get_realtime_indicators` 返回的 `historical` 数组包含最近 3 日盘后确认的 KDJ-K/D 值（Tushare stk_factor 前复权值）。Layer 1 已在调用此接口，直接取 `historical[0]`（昨日确认值）与当日 `realtime` 估算值比较即可判断交叉。

**替代方案**：单独调用 `get_technical` 获取 KDJ 历史序列。被否决——增加一次 HTTP 往返，且 `get_realtime_indicators` 已有足够数据。

### Decision 2: K 线形态识别 → 纯函数，基于 daily K-line OHLC

形态识别无需额外 API 调用——`check_entry_filters` 已经调用了 `get_daily_kline_qfq` 获取前复权日K线（用于 RSR 计算）。新增一个无副作用函数 `_detect_patterns(bars: list) -> dict`，从已有 K 线数据中提取最近两根日线的形态。

识别逻辑：
- **射击之星**: 实体 = |close - open|; 上影线 = high - max(open, close); 上影 ≥ 2x 实体 且 下影 ≤ 0.5x 实体
- **看跌吞没**: bar[-1] 收阳 且 bar[0] 收阴 且 bar[0].open > bar[-1].close 且 bar[0].close < bar[-1].open

### Decision 3: 量价背离 → 5 日窗口，纯函数

比较当日最高价是否为近 5 日最高，若是则检查当日成交量是否 ≥ 近 5 日最大成交量。若价格创新高但量未跟上 → 背离警告。

**替代方案**：用 OBV（能量潮）或 VPCI（量价确认指标）。被否决——复杂度高且数据要求更多，简单的"价量高点比较"在 A 股短周期中足够有效。

### Decision 4: 组织方式 → Layer 3 扩展为三合一

K 线形态和量价背离归入 Layer 3（超买/顶部过滤层），与该层已有的 RSI6/KDJ-J/CCI 共振逻辑并列。Layer 3 的最终结果 = max(超买过滤等级, 形态等级, 背离等级)。KDJ 死叉独立归入 Layer 1（技术面趋势判断）。

### Decision 5: 向后兼容

`LayerResult` 的 `details` 字段本身是 `list[str]`，新增的形态/背离检测结果直接追加到 details 中。不新增顶层响应字段，前端无需改动。

## Risks / Trade-offs

- **盘中 KDJ K/D 估算值误差**: `get_realtime_indicators` 的 KDJ-K/D 标记为 `intraday_estimate`，可能与盘后确认值有偏差。→ 死叉判定仅用于降级（降仓），不用于硬禁止，容错空间足够
- **K 线形态盘中不稳定**: 盘中 K 线的 OHLC 随行情变化，射击之星的上影线可能在收盘前才最终确认。→ 仅使用 `get_daily_kline_qfq` 返回的**已收盘日线**做形态判断，不基于当日未完成的 K 线
- **量价背离误判**: 缩量上涨在强势股中也可能继续走高。→ 仅触发警告级别（仅试探仓），不硬禁止
