# -*- coding: utf-8 -*-
"""wolf_no_chase.py — 「不追高」的**确定性**判据（2026-09-19 用户拍板 A，落地自台账 §29.1）。

## 语料（全部带行号/日期）

| 原话 | 出处 |
|---|---|
| 「跌下来可以找买点，**但是冲上去一定不能追**」 | 2025-04-03 |
| 「保持50%仓位，**任何时候不追高追涨**」 | 2025-10-09 |
| 「只要看见**没量就别追**，没意义」 | 2025-06-25 |
| 「如果**当日开盘高开快速拉升**，或者低开快速拉升**想追进去的**，**在下午2.00-2.30这个时间段进行回补**，
  这个时候确保**分时上涨放量，回调缩量**的情况下，去补**进攻板块里面涨得还不多的**」 | 2025-04-15（做T方法·条件6） |
| 「高开做什么，高开昨天获利盘全部出逃…」 | 2016-01-15 |

台账 §29.1 的结论：**不是「绝不追」，而是「不在开盘追」** —— 想追就延后到 **14:00–14:30**。

## 实现形态（本模块 = 执行层硬闸，买腿专用）

整条腿**保留活性**，不丢弃：开盘/平稳时段命中「追高」⇒ 本次不执行；同一条腿在每个 round 都会被重新评估，
到 **14:00–14:30** 自然会被放行 ⇒ 「延后到下午回补」是这么实现的（无需另建一条腿）。

判据（买腿）：
  · 现价 **高于**建议买价（premium > `WOLF_NO_CHASE_PREMIUM_PCT`，默认 0 = 没等到回踩就现价买）；**且**
  · 当日自低点拉升 ≥ `WOLF_NO_CHASE_RISE_PCT`（默认 **2%**）或 日内分位 ≥ `WOLF_NO_CHASE_QUANTILE`（默认 **80**）；
  · 且 **不在** 14:00–14:30（他给的"想追就在这个时段回补"）；
⇒ 三者同时成立才拦。低吸（现价 ≤ 建议买价、或没拉升）一律不受影响；卖腿完全不适用。

⚠️ **参数是自设**：他给了概念与时段，没给「拉升多少算追高」的数字 ⇒ 2% / 80 分位标为**待校**，
    在 `docs/wolf-buy-parameter-ledger.md` 的自设参数清单里登记。

开关 `WOLF_NO_CHASE`：**库内默认 0**（生产逐位不变），回测由 pins/驱动置 1。

────────────────────────────────────────────────────────────────
## D1（2026-09-21 用户拍板「把 D1 补上」）：**开盘段「快速拉升不追」**

上面那条判据的**参照物是"建议买价"**（挂的前低/线价），所以它只拦"**没回踩就现价买**"那一类；
  而 2026-01-12 苏州科达那种买腿（`wolf_zheng_t_buy`：**触发价 = 现价**，premium 恒 0）
  **天然豁免** ⇒ 开盘段冲到 +6.4% 也照买。D1 就是补这个洞。

语料 2025-04-15 条件6（**方向**）：
  「如果**当日开盘高开快速拉升**，或者**低开快速拉升想追进去的**，
    **在下午2.00-2.30这个时间段进行回补**…」
⇒ 参照物是**当日开盘价**（"高开快速拉升"/"低开快速拉升"都以开盘为起点），
  处置与上面一致：**本次不执行，腿保留活性，到 14:00–14:30 自然放行**。
  另有 2025-04-15 条件2 后半句「**尽量避免开盘直接买卖**」、2026-01-12「千万绝对不要开盘买」同向。

阈值 `WOLF_OC_RISE_PCT`（默认 **3.0%**）**属自设待校** —— 他只说"快速拉升"，没给数字。
  实测（`WOLF_OC_KINDS=*`、t1–t5 共 143 笔买成交、FIFO 已实现盈亏，`.dsh-tmp/d1d2_quant2.py`）：
  开盘段 0935–0945 共 42 笔；其中 **现价 ≥ 当日开盘+3% 的 5 笔 净 −1,398**（留下的 37 笔 +18,924）
  ⇒ 3% 档"拦对且影响面仅 3.5%"；+2% 档 7 笔 **−4,442**（更狠）；+1% 档 24 笔 **+7,268**（会误伤 2 万）。

开关 `WOLF_OPEN_CHASE` 默认 **0**（生产逐位不变）；`WOLF_OC_WINDOW=0930-0945`；
  `WOLF_OC_RISE_PCT=3.0`；`WOLF_OC_KINDS=*`（`*` = **所有买腿**，含 253 建仓腿与趋势突破腿）。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass


ON = str(os.getenv("WOLF_NO_CHASE", "0")).strip().lower() in ("1", "true", "yes", "on")
# 2026-09-19 用户拍板 C（调阈值）：判据由「拉升≥2% **或** 分位≥80」收紧为
#   「拉升 ≥ WOLF_NO_CHASE_RISE_PCT **且** 分位 ≥ WOLF_NO_CHASE_QUANTILE」 —— 只有**真贴日高**的才算追高。
#   实测 0106 六笔的对照：603893(3.15%/99.2)、603383(2.99%/98.2)、603019(4.11%/94.5) 仍拦；
#   002364(2.70%/80.7)、600183(2.23%/69.7) 会被放行；**601138(2.77%/84.06) 恰在 85 门槛下方 0.94pt** ⇒ 放行
#   （要连它一起拦，把 WOLF_NO_CHASE_QUANTILE 调到 84 即可 —— 该数字属自设待校）。
RISE_PCT = float(os.getenv("WOLF_NO_CHASE_RISE_PCT", "2.0"))        # 自设（待校）
QUANTILE = float(os.getenv("WOLF_NO_CHASE_QUANTILE", "85.0"))       # 自设（待校）
PREMIUM_PCT = float(os.getenv("WOLF_NO_CHASE_PREMIUM_PCT", "0.0"))  # 0 = 任何高于建议买价都算"没回踩"
PM_WINDOW = os.getenv("WOLF_NO_CHASE_PM", "1400-1430")              # 他给的"想追就在 14:00–14:30 回补"

# ── D1 开盘段「快速拉升不追」（见模块头；参照物 = **当日开盘价**，不是建议买价）──
OPEN_ON = str(os.getenv("WOLF_OPEN_CHASE", "0")).strip().lower() in ("1", "true", "yes", "on")
OPEN_WINDOW = os.getenv("WOLF_OC_WINDOW", "0930-0945")     # 开盘段（覆盖到我们第一根 5min 09:35–09:45）
OPEN_RISE_PCT = float(os.getenv("WOLF_OC_RISE_PCT", "3.0"))  # 自设（待校）
OPEN_KINDS = os.getenv("WOLF_OC_KINDS", "*")                # "*" = 所有买腿

_STATS: Dict[str, Any] = {"checked": 0, "chase_block": 0, "pm_pass": 0, "no_data": 0, "errors": 0,
                          "oc_checked": 0, "oc_block": 0, "oc_pass": 0, "oc_no_data": 0}


def enabled() -> bool:
    return ON


def open_enabled() -> bool:
    return OPEN_ON


def stats() -> Dict[str, Any]:
    return {"on": ON, "rise_pct": RISE_PCT, "quantile": QUANTILE, "premium_pct": PREMIUM_PCT,
            "pm": PM_WINDOW, "open_on": OPEN_ON, "oc_window": OPEN_WINDOW,
            "oc_rise_pct": OPEN_RISE_PCT, "oc_kinds": OPEN_KINDS, **_STATS}


def in_pm_window(hhmm: str) -> bool:
    """是否在他给的"想追就延后到这个时段"里（默认 14:00–14:30）。"""
    try:
        a, b = PM_WINDOW.split("-")
        return a.strip() <= str(hhmm) <= b.strip()
    except Exception:
        return False


def verdict(px: float, bid: float, rise_pct: Optional[float] = None,
            quantile: Optional[float] = None, hhmm: Optional[str] = None,
            side: str = "buy") -> Tuple[bool, str]:
    """返回 (ok, why)。ok=False = 本次不执行（腿保留，等下午窗口）。

    px=现价, bid=建议买价（腿的回踩价）, rise_pct=当日自低点涨幅%, quantile=日内分位%.
    卖腿、数据缺失一律放行（fail-open）。
    """
    if not ON:
        return True, "WOLF_NO_CHASE=0（不追高闸关闭）"
    if str(side or "buy").lower() not in ("buy", "买入"):
        return True, "卖腿不适用（只做T时段/止损另管）"
    _STATS["checked"] += 1
    try:
        _px, _bid = float(px or 0), float(bid or 0)
    except Exception:
        _STATS["errors"] += 1
        return True, "价格不可解析→放行"
    if _px <= 0 or _bid <= 0:
        _STATS["no_data"] += 1
        return True, "缺现价/建议买价→放行"
    _hm = str(hhmm or datetime.now().strftime("%H%M"))
    if in_pm_window(_hm):
        _STATS["pm_pass"] += 1
        return True, ("在他给的追高窗口(%s)内→放行（2025-04-15 条件6「想追进去的…在下午 2.00-2.30 进行回补」）"
                      % PM_WINDOW)
    premium = (_px / _bid - 1.0) * 100.0
    if premium <= PREMIUM_PCT:
        return True, "现价未高于建议买价(%.3f%% ≤ %.3f%%)→属回踩低吸，放行" % (premium, PREMIUM_PCT)
    _r = None if rise_pct is None else float(rise_pct)
    _q = None if quantile is None else float(quantile)
    # 2026-09-19 用户拍板 C：**且**语义（拉升够 + 分位够，两条都要）——只有真贴日高才算追高
    if _r is None or _q is None:
        _STATS["no_data"] += 1
        return True, ("缺拉升(%s)/分位(%s)之一 → 按 fail-open 放行"
                      % ("?" if _r is None else "%.2f" % _r, "?" if _q is None else "%.1f" % _q))
    hot = bool(_r >= RISE_PCT and _q >= QUANTILE)
    if not hot:
        return True, ("高于买价 %.3f%% 但未同时满足『拉升≥%.1f%% 且 分位≥%.0f』"
                      "（自低点 %.2f%%／分位 %.1f）→ 放行"
                      % (premium, RISE_PCT, QUANTILE, _r, _q))
    _STATS["chase_block"] += 1
    return False, ("不追高：现价高于建议买价 %.3f%%，且当日自低点已拉升 %s%%／日内分位 %s —— "
                   "他 2025-04-03「冲上去一定不能追」、2025-10-09「任何时候不追高追涨」；"
                   "想追按 2025-04-15 条件6 延后到 %s 回补（本腿保留活性，到点会被重新评估）"
                   % (premium, "?" if _r is None else "%.2f" % _r, "?" if _q is None else "%.1f" % _q, PM_WINDOW))

# ─────────────────────────── D1：开盘段「快速拉升不追」 ───────────────────────────
def oc_window() -> Tuple[str, str]:
    """开盘段范围（默认 0930-0945），解析失败退回默认。"""
    try:
        a, b = str(OPEN_WINDOW).split("-")
        a, b = a.strip().replace(":", ""), b.strip().replace(":", "")
        if len(a) == 4 and len(b) == 4 and a.isdigit() and b.isdigit():
            return a, b
    except Exception as _e_sil1:
        _silent_alert("wolf_no_chase.py:161", _e_sil1)
    return "0930", "0945"


def oc_kinds() -> list:
    return [x.strip() for x in str(OPEN_KINDS or "*").split(",") if x.strip()]


def oc_applies(kind: Optional[str]) -> bool:
    """该腿型是否受 D1 约束（`WOLF_OC_KINDS=*` ⇒ 所有买腿；否则按白名单）。"""
    ks = oc_kinds()
    if "*" in ks or not ks:
        return True
    return str(kind or "").strip() in ks


def in_oc_window(hhmm: Optional[str] = None) -> bool:
    hm = str(hhmm or datetime.now().strftime("%H%M")).replace(":", "")
    a, b = oc_window()
    return a <= hm < b


def open_verdict(px: float, open_px: float, hhmm: Optional[str] = None,
                 side: str = "buy", kind: Optional[str] = None) -> Tuple[bool, str]:
    """D1 判定：开盘段内、**现价 ≥ 当日开盘 × (1+WOLF_OC_RISE_PCT%)** ⇒ 本次不执行。

    返回 (ok, why)。ok=False = 本次不执行（腿保留活性，到 14:00–14:30 或价落回后自然放行）。
    卖腿、非开盘段、腿型不在白名单、数据缺失 → 一律放行（fail-open）。
    """
    if not OPEN_ON:
        return True, "WOLF_OPEN_CHASE=0（开盘不追高闸关闭）"
    if str(side or "buy").lower() not in ("buy", "买入"):
        return True, "卖腿不适用（只做T时段/止损另管）"
    if not oc_applies(kind):
        return True, "腿型 %s 不在 WOLF_OC_KINDS=%s 内→放行" % (kind, OPEN_KINDS)
    hm = str(hhmm or datetime.now().strftime("%H%M")).replace(":", "")
    if not in_oc_window(hm):
        return True, "不在开盘段 %s-%s（现在 %s）→放行" % (oc_window()[0], oc_window()[1], hm)
    _STATS["oc_checked"] += 1
    if in_pm_window(hm):
        _STATS["oc_pass"] += 1
        return True, "在他给的追高窗口(%s)内→放行" % PM_WINDOW
    try:
        _px, _op = float(px or 0), float(open_px or 0)
    except Exception:
        _STATS["oc_no_data"] += 1
        return True, "价格不可解析→放行"
    if _px <= 0 or _op <= 0:
        _STATS["oc_no_data"] += 1
        return True, "缺现价/当日开盘价→放行（fail-open）"
    prem = (_px / _op - 1.0) * 100.0
    if prem < OPEN_RISE_PCT:
        _STATS["oc_pass"] += 1
        return True, "距当日开盘 +%.2f%% < %.1f%%→不是快速拉升，放行" % (prem, OPEN_RISE_PCT)
    _STATS["oc_block"] += 1
    return False, ("开盘快速拉升不追：现价 %.3f 距**当日开盘** %.3f 已 **+%.2f%%**（≥%.1f%%，自设待校）"
                   "—— 他 2025-04-15 条件6「如果当日开盘高开快速拉升，或者低开快速拉升想追进去的，"
                   "在下午2.00-2.30这个时间段进行回补」＋条件2「尽量避免开盘直接买卖」、"
                   "2026-01-12「千万绝对不要开盘买」；本腿保留活性，到 %s 会被重新评估"
                   % (_px, _op, prem, OPEN_RISE_PCT, PM_WINDOW))
