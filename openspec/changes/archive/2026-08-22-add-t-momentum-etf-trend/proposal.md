# Proposal: t 账户科技 ETF 动量趋势（结合 arkvol 贪婪门控）

## Why

V反 策略（t-vrebounce / t-vreb-etf）只捕捉"暴跌后 8 天反弹"，结构性错过主升浪（如 2026-07 半导体 +48%，V反 在 5-27 触发后 8 天内已离场）。回测证明 tech7 池"20 日动量双周轮动 TOP3 + 贪婪分位>0.9 空仓"在 2024-2026 年化 +62%（理想口径），2026 震荡年 +44%——补上"吃主升浪"的另一半，与 V反 互补。

## What Changes

- 新增 t 账户**科技 ETF 动量趋势**短线信号（与股票/科技ETF V反 并行，账户隔离不变）：
  - 池：tech7（创业板50/半导体/人工智能/5G通信/大数据/通信设备/科创芯片，与 arkvol tech-hardware-greed 对齐）
  - 信号：20 日动量降序取 TOP3 等权；每 10 个交易日（双周）轮动调仓
  - 贪婪门控：标的 250 日贪婪分位 > 0.9 剔除（空仓等待）；数据源 arkvol tech-hardware-greed
  - 出场：调仓日自然换出（动量掉出 TOP3 卖出）；**不做独立止损**（回测证明动量转负止损是负资产：年化 62%→41%）
- 建仓/平仓复用 t 网关（build_t_position build_mode='mom_etf'，短线档 sizing 30%），T+1 由网关保证
- 新监控器 MomEtfMonitor（env T_MOM_ETF_ENABLED 灰度）+ 候选/事件落库（source='mom_etf'）+ API
- 复用 golden_pit_sector_service 的动量/贪婪信号与 fund_daily K 线（TTL 缓存），不重复造轮子

## Capabilities

- **New Capabilities**:
  - `momentum-etf-trend`：t 账户科技 ETF 动量趋势信号的选股、门控、调仓、执行与账户隔离行为
- **Modified Capabilities**: 无（t-vrebounce 等现有行为不变；黄金坑 sector_selection 只读复用）

## Impact

- 代码：新增 `backend/app/services/t_mom_etf.py`；修改 `t_build.py`（build_mode 档）、`worker_main.py`、`api/t_account.py`、前端 TAccountPage（可选 tab）
- 数据：复用 t_vreb_daily（fund_daily 落库）+ arkvol tech-hardware-greed（已有 TTL 缓存）；**arkvol 贪婪历史仅 2025-01 起**（门控验证段 1.5 年，上线后持续积累）
- 依赖：tushare fund_daily、arkvol API（生产已验证可用）
- 风险：贪婪数据 2025 前缺失（门控在 2025-01 前自动退化为无门控）；回撤 -27%（动量本性，仓位档 30% 缓解）