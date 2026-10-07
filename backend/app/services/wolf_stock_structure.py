# -*- coding: utf-8 -*-
"""wolf_stock_structure.py — 买腿的**个股结构门**（2026-09-19 用户拍板 A）。

## 为什么（用户 2026-09-19 问「看 jan11 0128–0130 的操作，是否存在下跌趋势买入的迹象？」）

实测 jan11 在 0128–0130 有 4 笔买入，其中两笔是**在个股下跌结构里接刀**（用买入前一日收盘算的均线）：
  · 0128 11:05 SH603019 100@91.229：MA5 95.56 / MA10 94.48 / MA20 91.83 ⇒ **同时破三条均线**，
    1 月从 101.0 一路下行 ⇒ 0130 卖 89.88，**−148**；
  · 0128 13:40 SZ002579 800@12.318：MA5 12.63 / MA10 12.53 / MA20 12.37 ⇒ **同时破三条均线**，
    且是给已亏损持仓**加仓** ⇒ 0129 尾盘卖 3300@11.89，**−1,550**（三天最大单笔亏损）。
另两笔（002518 只破 MA5、仍在 MA10/MA20 上方；603068 均线附近横盘）不属下跌趋势买入。

**根因**：正T买入腿（`wolf_zheng_t_buy`）由 `wolf_t_rules` 的形态触发（低吸价差／量能／regime），
**不看个股 stage、也不看均线排列**；现有闸只能拦「破**前低**（254 口径）／跌停／急杀／指数破位／
板块主类／可买门／追高」——而这两笔的现价**都在 20 日低点之上**，只是跌破 MA5/10/20 ⇒ 一道都碰不到。
指数层没错：0128–0130 指数站上 60/144/200（`index_breakdown` 全 False），和他 2026-01-26
「指数没破位就没有悲观的理由」一致 ⇒ 问题在**个股层**。

## 语料

· `docs/wolf-behavior-blueprint.md:149`「主升 75%／调整 50%／有风险 30／**下跌不做**」——本门即「下跌不做」；
· 既有口径「挖坑段不加」「结构未确认不接」；
· 他 2025-04-03「跌下来可以找买点」指的是**上升趋势里的回调**（同句后半是「冲上去一定不能追」），
  不是「下跌趋势里越跌越买」。

## 判据（买腿专用）

  现价 < MA20 **且** MA10 < MA20 ⇒ **不买**（明确的下跌结构）；
  其余放行（只破 MA5 的回调、站上 MA20 的震荡/上升都照旧）。

⚠️ 阈值为结构性定义（MA20 + 空头排列），不是自设数字；数据不足（<20 根日K）一律 **fail-open**。
开关 `WOLF_STOCK_TREND_GATE`：**库内默认 0**（生产逐位不变），回测由 pins/驱动置 1。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

ON = str(os.getenv("WOLF_STOCK_TREND_GATE", "0")).strip().lower() in ("1", "true", "yes", "on")
MIN_BARS = int(os.getenv("WOLF_STOCK_TREND_MIN_BARS", "20"))

_STATS: Dict[str, Any] = {"checked": 0, "blocked": 0, "no_data": 0, "errors": 0}


def enabled() -> bool:
    return ON


def stats() -> Dict[str, Any]:
    return {"on": ON, "min_bars": MIN_BARS, **_STATS}


def _ma(vals: List[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / float(n)


def is_downtrend(price: float, bars: Optional[List[dict]] = None) -> bool:
    """**纯结构判定**（不受开关影响，供卖出侧的"趋势转弱"豁免复用）：现价 < MA20 且 MA10 < MA20。"""
    try:
        px = float(price or 0)
        closes = [float(b.get("close") or 0) for b in (bars or []) if b and float(b.get("close") or 0) > 0]
    except Exception:
        return False
    if px <= 0 or len(closes) < MIN_BARS:
        return False
    ma10, ma20 = _ma(closes, 10), _ma(closes, 20)
    return bool(ma10 and ma20 and px < ma20 and ma10 < ma20)


def verdict(symbol: str, price: float, bars: Optional[List[dict]] = None) -> Tuple[bool, str]:
    """返回 (ok, why)。ok=False ⇒ 该买腿不执行（下跌结构，蓝图:149「下跌不做」）。

    bars: `[{"date","close","high","low","vol"}, …]`（升序，**不含当日**；`t_monitor._daily_dated` 的口径）。
    数据不足／价格缺失 ⇒ 放行（fail-open，绝不因为取不到数据就禁止买入）。
    """
    if not ON:
        return True, "WOLF_STOCK_TREND_GATE=0（个股结构门关闭）"
    _STATS["checked"] += 1
    try:
        px = float(price or 0)
    except Exception:
        _STATS["errors"] += 1
        return True, "价格不可解析→放行"
    try:
        closes = [float(b.get("close") or 0) for b in (bars or []) if b and float(b.get("close") or 0) > 0]
    except Exception:
        _STATS["errors"] += 1
        return True, "日K不可解析→放行"
    if px <= 0 or len(closes) < MIN_BARS:
        _STATS["no_data"] += 1
        return True, "日K不足(%d<%d)或缺现价→放行" % (len(closes), MIN_BARS)
    ma10, ma20 = _ma(closes, 10), _ma(closes, 20)
    if ma10 is None or ma20 is None:
        _STATS["no_data"] += 1
        return True, "均线不可算→放行"
    if px < ma20 and ma10 < ma20:
        _STATS["blocked"] += 1
        return False, (f"个股下跌结构：现价 {px:.3f} < MA20 {ma20:.3f} 且 MA10 {ma10:.3f} < MA20 ⇒ 不做"
                       "（蓝图:149「主升75%/调整50%/有风险30/下跌不做」；同族口径「挖坑段不加」）")
    return True, "结构未走坏（现价 %.3f / MA10 %.3f / MA20 %.3f）" % (px, ma10, ma20)