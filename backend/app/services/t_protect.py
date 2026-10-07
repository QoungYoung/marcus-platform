# -*- coding: utf-8 -*-
"""t_protect.py — 「保护性卖出」的**结构化**判定（用户 2026-09-22 拍板 "A′"）。

## 问题（同一族 bug 的第三、四处）

项目里有三处用**调用方传进来的 reason 自由文本**判断"这笔卖出是不是保护性动作"：

| 位置 | 旧判据 | 后果 |
|---|---|---|
| `t_gateway.trade_cap_ok`（G6 一个票最多卖 2 笔） | `"止损" in reason or "破位" in reason` | 理由里写「**未跌破止损**」⇒ 命中关键词 ⇒ **豁免**，第 3 笔照卖 |
| `t_gateway._sell_time_gate_ok`（13:00–14:30 卖出禁区） | 8 个关键词 | 同上 |
| `t_turnover.check`（①②③④ 换手闸） | `_PROTECT` 8 个关键词 | 同上（买腿已在 "A" 里修掉文本豁免，卖腿还留着） |

而 `reason` 是 **AI 决策理由**：AI 写「未跌破止损」「不属破位」「无风控否决」这类**否定句**是常态 ⇒
闸门被自己的判据文本绕过。实测（T6/drabt6 2026-01-07 09:35）：同一分钟、同一价 39.98，
三条止盈腿（`wolf_profit_take_sell` 500 / `wolf_fib_target_sell` 600 / `wolf_defensive_t_reduce` 300）
**各自放行** ⇒ 一次兑现变成 1,400 股（持仓 1,800）⇒ 该票两天清仓，而它到 0120 涨到 51.01
（对照：T5 同期只卖 700 股、留住大头，该票已实现 +11,003 vs T6 +2,427）。

## 口径

**保护性 = 「止血/风控」类离场腿**，按**结构化腿型**判定（`t_triggers.event_type` / `t_conditions.trigger_kind`）：

| 腿型 | 语义 |
|---|---|
| `stop_loss` | 止损 |
| `custom_support_sell` | 破位/破支撑离场（G3 删票也是挂它） |
| `wolf_passive_stop_sell` | 被动**止盈**线跌破（狼大 2025-06-09「不破不卖」；**原文误标「被动止损」**，2026-09-23 更正）|
| `derisk_cut` | 风控减仓 |

**不在**名单里的（止盈/高抛/斐波目标/防御性减仓/黄线离场…）⇒ 照常受 G6 笔数上限、卖出时点门、
换手闸约束 —— 它们是**主动动作**，不是止血。

⚠️ 与 `t_capacity._BASE_EXEMPT_KINDS`（能否**穿透底仓**）不是一回事：那是"能卖哪些股"，
本模块是"算不算保护性动作（豁免计数/时点/换手约束）"，两者名单**故意不重合**
（例如 `wolf_defensive_t_reduce` 允许穿透底仓，但**不**豁免笔数上限）。

## 开关

`WOLF_PROTECT_STRUCTURED`（**库内默认 0** = 逐字旧行为，生产零影响；回测由 pins 置 1）；
`WOLF_PROTECT_KINDS` 可覆盖名单（逗号分隔）。取不到腿型时按"非保护性"处理（保守：不豁免）。
"""
from __future__ import annotations

import os
from typing import Optional, Set

ENV = "WOLF_PROTECT_STRUCTURED"
DEFAULT_KINDS = "stop_loss,custom_support_sell,wolf_passive_stop_sell,derisk_cut"
# 旧行为用的 8 个关键词（只在开关关时使用）
PROTECT_KW = ("止损", "破位", "被动止盈", "顶态", "清仓", "避险", "风控")


def structured_on() -> bool:
    return str(os.getenv(ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


def kinds() -> Set[str]:
    raw = os.getenv("WOLF_PROTECT_KINDS", DEFAULT_KINDS) or ""
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def is_protective(trigger_kind: Optional[str] = None, is_stop_loss: bool = False,
                  reason: str = "", side: str = "sell") -> bool:
    """这笔委托是否属于**保护性动作**（止血/风控）——**只对卖出成立**。

    ⚠️ `side` 必须传（2026-09-22 自查抓到的回归）：本函数是"卖出语义"，若不区分买卖，
      买腿只要理由里带「未跌破止损」就会被判保护性 ⇒ 把 "A"（买腿不再被理由文本旁路）的修复**又撤回**。
    开关关 ⇒ 逐字旧行为（`is_stop_loss` 或 reason 命中 8 个关键词）；
    开关开 ⇒ 只认 `is_stop_loss` 与**结构化腿型**白名单；reason 文本**不再**参与判定。
    """
    if str(side or "sell").strip().lower() in ("buy", "买入"):
        return False                      # 买入没有"保护性委托"一说
    if is_stop_loss:
        return True
    if not structured_on():
        return any(k in str(reason or "") for k in PROTECT_KW)
    k = str(trigger_kind or "").strip().lower()
    return bool(k) and (k in kinds())
