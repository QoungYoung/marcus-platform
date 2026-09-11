# -*- coding: utf-8 -*-
"""wolf_trade_window.py — 日内做T的**正向时间窗**（A5，2026-09-11）。

────────────────────────────────────────────────────────────────
狼大原话（XLS 2025-04-15「我的买卖做T方法」条件 2）:
  「**当日只做上午 9.45-10.00 下午 2.00-2.30 这两个时间段的交易**，
    尽量避免开盘直接买卖和平稳时间的来回T（**有消息刺激的个股例外**）」
（同一篇的适用范围声明：「这里说的买卖方法一般对应的 **单日或者 3 日内**，
  如果是**大级别的买入和卖出是另外一个方法**」）
────────────────────────────────────────────────────────────────

**为什么需要它**：我们此前只有**禁止**区间（14:45 后禁新开仓；13:00–14:30 禁止执行止损），
**没有他的正向时间窗** —— 即"什么时候允许动手"这一半是缺的（§11.4 P1）。

**适用面（按他自己的范围声明界定，不扩大）**：
  · **仅适用于"日内做T"类买腿**：`low_buy`（正T低吸）、`custom_prevlow`（挂前低回踩低吸，
    2025-03-06「挂前一天的低点 能买进去就做正T」）；
  · **不适用于**：`custom_m5dump`（253 大盘急杀开小底仓 —— 属**建仓/大级别**那套，他自己划了界）、
    所有**卖腿**（当日卖出条件是他另给的一张清单）、**止损**（已有独立的 ④ 时点门 13:00–14:30，不叠加）。

**"有消息刺激的个股例外"**：我们没有可靠的"消息刺激"判据（公告判据只覆盖"利空事件"，不等于"刺激"），
  **故默认不实现该例外**（`WOLF_TW_NEWS_EXEMPT` 保留但默认 0），并在提示里标注——不拿不可靠数据当豁免理由。

开关：`WOLF_TRADE_WINDOW=0` 整体关闭；`WOLF_TW_AM=0945-1000` / `WOLF_TW_PM=1400-1430` 调窗口；
      `WOLF_TW_KINDS=low_buy,custom_prevlow` 调适用腿型。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional, Tuple

DEFAULT_AM = "0945-1000"
DEFAULT_PM = "1400-1430"
DEFAULT_KINDS = "low_buy,custom_prevlow"


def enabled() -> bool:
    return os.getenv("WOLF_TRADE_WINDOW", "1").strip() not in ("0", "false", "no")


def _parse(spec: str) -> Optional[Tuple[str, str]]:
    try:
        a, b = str(spec).split("-")
        a, b = a.strip(), b.strip()
        if len(a) == 4 and len(b) == 4 and a.isdigit() and b.isdigit():
            return a, b
    except Exception:
        pass
    return None


def windows() -> List[Tuple[str, str]]:
    out = []
    for k, d in (("WOLF_TW_AM", DEFAULT_AM), ("WOLF_TW_PM", DEFAULT_PM)):
        w = _parse(os.getenv(k, d))
        if w:
            out.append(w)
    return out


def kinds() -> List[str]:
    return [x.strip() for x in (os.getenv("WOLF_TW_KINDS", DEFAULT_KINDS) or "").split(",") if x.strip()]


def applies_to(trigger_kind: Optional[str]) -> bool:
    """该腿型是否受时间窗约束（按他"单日/3日内"的范围声明白名单）。"""
    return str(trigger_kind or "").strip() in kinds()


def allowed(now: Optional[datetime] = None) -> Tuple[bool, str]:
    """当前是否在允许的交易时段内 → (ok, reason)。"""
    if not enabled():
        return True, "WOLF_TRADE_WINDOW=0（时间窗关闭）"
    try:
        hm = (now or datetime.now()).strftime("%H%M")
    except Exception:
        return True, "时间不可用→放行"
    ws = windows()
    for a, b in ws:
        if a <= hm < b:
            return True, "在狼大做T时段内(%s-%s)" % (a, b)
    rng = " / ".join("%s-%s" % (a, b) for a, b in ws)
    news = ""
    if os.getenv("WOLF_TW_NEWS_EXEMPT", "0").strip() in ("1", "true", "yes"):
        news = "；消息刺激例外已开(但无可靠数据源, 仅记录)"
    return False, ("不在狼大做T时段(%s)：他 2025-04-15「当日只做上午 9.45-10.00 下午 2.00-2.30 这两个时间段"
                   "的交易，尽量避免开盘直接买卖和平稳时间的来回T」%s" % (rng, news))


def directive() -> str:
    """给上下文的提示句（当前是否在窗口内）。

    ⚠️ 2026-09-11：关闭（`WOLF_TRADE_WINDOW=0`）时**不得**再输出"时段…允许"——
    那会被读成"时间窗仍在生效"。改为明确说明已关闭。
    """
    if not enabled():
        return ("⏱ 日内做T时段：**已关闭**（`WOLF_TRADE_WINDOW=0`）。"
                "依据 2026-09-11 回测：两种独立口径下窗内都无正贡献（详见 "
                "`docs/backtest-batch2-design.md` §17/§18），故按用户决定关闭。")
    ws = windows()
    rng = " / ".join("%s-%s" % (a, b) for a, b in ws)
    try:
        hm = datetime.now().strftime("%H:%M")
    except Exception:
        hm = "?"
    ok, _ = allowed()
    return ("⏱ 日内做T时段：狼大 2025-04-15 条件2「当日只做 %s 这两个时间段」（现在 %s → %s）"
            % (rng, hm, "允许" if ok else "**不在窗口，不新开日内T腿**"))
