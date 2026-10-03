# -*- coding: utf-8 -*-
"""t_leg_kinds.py —— **腿类型（trigger_kind）的单一来源**（账本 §9.496，用户「拒绝硬编码」）

**为什么有它**：同一份 kind 名单原先**硬编码在 5 处**（t_bridge、t_gateway、t_monitor ×3）
⇒ 加一个腿类型（例：buy_255）要改 5 个地方，漏一处就**静默失效**
⇒ ⇒ **现改成：这里定义一次，各处 import 用** —— 加新类型只改本文件一处

⚠️ **语义逐字保留**：下面两个元组＝替换前各处的**原字面值**
"""
from __future__ import annotations


# 买腿：可作为「买入条件腿」被识别
BUY_LEG_KINDS = (
    "wolf_zheng_t_buy",
    "low_buy",
    "custom_prevlow",
    "custom_m5dump",
    "custom_buy",
    "wolf_ambush_buy",
    "trend_break_buy",
    "buy_253",
    "buy_254",
    "wolf_build",
)

# 低吸类买腿：金额/档位/量能按「低吸」规则处理
LOWDIP_KINDS = (
    "custom_prevlow",
    "custom_m5dump",
    "low_buy",
    "wolf_253_build",
    "wolf_253_refill",
    "wolf_254_refill",
    "wolf_254_build",
)

PREVLOW_M5_KINDS = ("custom_prevlow", "custom_m5dump")   # 触前低 ＋ 5 分钟急杀

# 建仓腿型：无底仓时「建仓规模」只对这些腿型计算（语料前置门②资格）
T_BUILD_KINDS = (
    "custom_prevlow",
    "custom_m5dump",
    "low_buy",
    "wolf_ambush_buy",
)


def is_buy_leg(kind) -> bool:
    return str(kind) in BUY_LEG_KINDS


def is_lowdip(kind) -> bool:
    return str(kind) in LOWDIP_KINDS


def is_prevlow_m5(kind) -> bool:
    return str(kind) in PREVLOW_M5_KINDS


if __name__ == "__main__":
    print("BUY_LEG_KINDS %d: %s" % (len(BUY_LEG_KINDS), ",".join(BUY_LEG_KINDS)))
    print("LOWDIP_KINDS  %d: %s" % (len(LOWDIP_KINDS), ",".join(LOWDIP_KINDS)))
    print("PREVLOW_M5    %d: %s" % (len(PREVLOW_M5_KINDS), ",".join(PREVLOW_M5_KINDS)))
