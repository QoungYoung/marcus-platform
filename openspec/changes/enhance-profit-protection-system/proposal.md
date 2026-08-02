## Why

7/14-7/24 亏损分析揭示了两个独立但互补的缺陷：(1) Iron Rule 2 移动止盈存在 3-4% 的"保护真空区"——当浮盈从高位暴跌至 T1 阈值以下时，所有保护层同时失效，拓维信息 (+12%→+2%→恐慌清仓) 和紫光股份 (+12%→亏损) 的亏损本可以避免；(2) 现有止损体系纯粹是"价格已跌后的被动防守"，没有利用 KDJ/RSI 超买信号做主动止盈——光线传媒 (KDJ=81, RSI=76)、紫光股份 (KDJ=90, RSI=82) 的暴跌前都发出了 3-5 天的超买预警，但系统完全无视。

## What Changes

- **P1: Iron Rule 2 真空区修复**：保护层级判定从"当前浮盈"改为"会话最高浮盈"。一旦某层级被激活，当日该层级的保护线不再下移，消除 T1 以下到 HWM 增强之间的无保护区
- **P2: 超买止盈规则（规则 2.3）**：新增三条递进式主动止盈规则，依赖 KDJ_K / RSI6 / 单日涨幅信号，在技术面过热时主动减仓：
  - KDJ_K ≥ 80 首次触发 → 减仓 30%
  - KDJ_K ≥ 80 + RSI6 ≥ 75 + 单日涨幅 > 3% → 减仓 50%
  - KDJ_K ≥ 80 连续 3 个交易日 → 强制清仓
- 规则 2.3 插入优先级链：0a→0b→1→2→**2.3**→2.5→2.6→3，位于铁律二之后、技术背离之前
- 数据获取复用现有 `_tech_divergence.py` 的 Tushare + 腾讯实时链路，新增 `get_overbought_indicators()` 函数暴露原始 KDJ_K / RSI6 / daily_change_pct 值

## Capabilities

### New Capabilities
- `overbought-take-profit`: 基于 KDJ/RSI 超买信号的主动止盈规则系统，在技术面过热时递进式减仓（30%/50%/100%），于价格尚未下跌时锁定利润

### Modified Capabilities
- `trading`: Iron Rule 2（铁律二移动止盈）的保护层级判定由当前浮盈改为会话峰值浮盈，消除 T1 以下的保护真空区

## Impact

- `backend/app/services/stop_loss_monitor.py` — 新增 `_session_max_float` 追踪、`_check_iron_rule2` 改用峰值判定、新增 `_check_overbought_take_profit` 方法、`_evaluate_stop_rules` 增加规则 2.3 调用
- `backend/app/core/trading/_tech_divergence.py` — 新增 `get_overbought_indicators()` 函数，返回 KDJ_K / RSI6 / daily_change_pct 原始值
- 不改变规则 0a、0b、1、2.5、2.6、3 的行为，不影响 API 接口，不新增数据库表
