# -*- coding: utf-8 -*-
"""wolf_boll_levels.py — A10「BOLL 上轨卖出」（2026-09-11）。

═══════════════════════════════════════════════════════════════════════════
狼大原文（全部带日期，逐字）:
  · 2025-04-15 当日卖出条件清单「…**个股在区间震荡的时候碰到了自己各种压力位，比如均线或BOLL上轨**…」
  · **2026-04-29**「当出现 股价分时毫无理由地急速拉升，**触及上方大级别压力位(如BOLL上轨)时，
    逢高卖出部分底仓、锁定利润**」      ← 最可操作的一条：**触发=触及上轨 / 动作=卖部分锁利**
  · 2026-01-13「我长线游戏和稀土偏离太多 **先卖一半 等回归BOLL轨内再接回**」 ← 动作口径：**卖一半**
  · 2025-05-13「**顶部阶段**，每一天的支撑位都是上一天的最高位…**全止盈的位置就放在日线BOLL中轨附近，
    放量跌破收盘完全止盈**」（与他 2026-01-29「**收盘跌破我才出**」的确认口径一致 → 中轨侧必须**收盘**确认）
    ⚠️ **「顶部阶段」是他这句的前提，不是可省的字**。回测（2026-09-11，245 个交易日、5407 只、逐日横截面）：
      · **不加前提（全样本）**：跌破中轨后反而略强（逐日差 +0.159%、52.1% 天数）→ **看似不成立**
      · **加上"大盘高位"**（全市场等权处于近 120 日区间上 20% 分位）：跌破组中位 **−1.52%**/胜率 36.6%
        vs 未跌破 −0.90%/43.8%，**逐日横截面差 −1.994%、仅 18.8% 的天数反向** → **成立**
      · 只按"个股高位"（近 60 日区间上 20% 分位）不加大盘前提：差 −0.045% ≈ 无 → **前提必须是大盘顶部**
      ⇒ 所以本模块的中轨侧**强制要求大盘处于顶部区间**，否则不触发。
      ⚠️ 该结论的可比天数只有 16 天（独立样本不足 100）→ **属探索性**，待拉长区间复核。
  · 2026-02-09 / 04-23 / 05-22 / 06-15 / 06-23 / 07-09：BOLL **中轨** 当强弱分界（大盘/板块层面）
═══════════════════════════════════════════════════════════════════════════

**落点**
  · **上轨侧（默认开）**：持仓**触及/上穿日线 BOLL(20,2) 上轨** 且**浮盈 > 0** → 写一条
    `wolf_boll_upper_sell` 触发（**减半**锁利，与 board_half 同一执行管道），**当日去重**。
    依据 2026-04-29 + 2026-01-13。`WOLF_BOLL_SELL=0` 关。
  · **中轨侧（默认关，提示层）**：**放量收盘跌破**日线 BOLL 中轨 → 「完全止盈」。
    他这句 2025-05-13 是描述**顶部阶段**的做法，与我们既有六层止损可能有叠加 → 默认只提示不自动卖，
    `WOLF_BOLL_MID_EXIT=1` 才纳入自动卖出。

**口径**：BOLL(20, 2) 用**日线收盘价**（N/倍数是标准参数，他未另行指定；`WOLF_BOLL_N`/`WOLF_BOLL_K` 可调）。
「触及」= 当日最高价 ≥ 上轨（含上穿）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

_CACHE: Dict[str, Any] = {}


def enabled() -> bool:
    return os.getenv("WOLF_BOLL_SELL", "1").strip() not in ("0", "false", "no")


def mid_exit_enabled() -> bool:
    return os.getenv("WOLF_BOLL_MID_EXIT", "0").strip() in ("1", "true", "yes")


def _env_i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, "") or default))
    except (TypeError, ValueError):
        return default


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def boll(closes: List[float], n: Optional[int] = None,
         k: Optional[float] = None) -> Optional[Dict[str, float]]:
    """BOLL(n, k) → {mid, upper, lower, std}；样本不足 → None。"""
    nn = int(n or _env_i("WOLF_BOLL_N", 20))
    kk = float(k if k is not None else _env_f("WOLF_BOLL_K", 2.0))
    if not closes or len(closes) < nn:
        return None
    win = [float(x) for x in closes[-nn:]]
    mid = sum(win) / nn
    var = sum((x - mid) ** 2 for x in win) / nn          # 总体标准差（行情软件口径）
    std = var ** 0.5
    return {"mid": round(mid, 4), "upper": round(mid + kk * std, 4),
            "lower": round(mid - kk * std, 4), "std": round(std, 4), "n": nn, "k": kk}


def levels(symbol: str, force: bool = False) -> Optional[Dict[str, Any]]:
    """个股日线 BOLL（5 分钟缓存）。取数失败 → None。"""
    import time
    now = time.time()
    c = _CACHE.get(symbol)
    if not force and c and now - c["at"] < 300:
        return c["value"]
    try:
        from app.services.support_resistance import get_daily_bars
        bars = get_daily_bars(symbol, 60) or []
        closes = [float(b["close"]) for b in bars if b.get("close")]
        b = boll(closes)
        if not b:
            return None
        prev_vol = float(bars[-2].get("vol") or 0) if len(bars) >= 2 else 0.0
        v = {"symbol": symbol, **b,
             "last_close": closes[-1] if closes else None,
             "last_high": float(bars[-1].get("high") or 0) if bars else None,
             "last_vol": float(bars[-1].get("vol") or 0) if bars else None,
             "prev_vol": prev_vol,          # 供"放量跌破"判定（他 2025-05-13「放量跌破收盘」）
             "bars": len(closes)}
        _CACHE[symbol] = {"at": now, "value": v}
        return v
    except Exception as e:
        print(f"[boll] {symbol} 取数失败: {type(e).__name__}: {str(e)[:70]}")
        return None


def touch_upper(lv: Optional[Dict[str, Any]], price: Optional[float] = None,
                high: Optional[float] = None) -> bool:
    """是否触及/上穿上轨（用当日最高价，含上穿）。"""
    if not lv:
        return False
    ref = high if (high and high > 0) else price
    if not ref:
        ref = lv.get("last_high") or lv.get("last_close")
    return bool(ref and float(ref) >= float(lv["upper"]))


def active_sells(portfolio: Any, quotes: Optional[Dict[str, Any]] = None,
                 now: Any = None) -> Dict[str, Any]:
    """按他 2026-04-29 + 2026-01-13 → 上轨侧的**减半锁利**卖点（要求浮盈>0）。

    portfolio: dict / json 串，含 positions[{symbol, avg_cost/avg_price, volume}]。
    quotes:    {symbol: {current, pre_close, high?}}
    """
    out = {"enabled": enabled(), "active_sells": [], "directive": ""}
    if not enabled():
        return out
    pos = portfolio
    if isinstance(pos, str):
        try:
            import json
            pos = json.loads(pos)
        except Exception:
            pos = None
    if not isinstance(pos, dict):
        return out
    q = quotes or {}
    for p in (pos.get("positions") or []):
        sym = str(p.get("symbol") or "").upper()
        if not sym:
            continue
        cost = float(p.get("avg_cost") or p.get("avg_price") or 0)
        vol = float(p.get("volume") or 0)
        if cost <= 0 or vol <= 0:
            continue
        qt = q.get(sym) or {}
        cur = float(qt.get("current") or 0)
        if cur <= 0:
            continue
        lv = levels(sym)
        if not lv:
            continue
        if not touch_upper(lv, cur, qt.get("high")):
            continue
        if cur <= cost:                       # 「锁定利润」的前提是有利润
            continue
        out["active_sells"].append({
            "symbol": sym, "price": cur, "avg_cost": cost,
            "boll_upper": lv["upper"], "reduce_ratio": 0.5,
            "reason": ("触及日线BOLL上轨(%.2f)且浮盈%.2f%% → 减半锁利"
                       "（狼大 2026-04-29「触及上方大级别压力位(如BOLL上轨)时，逢高卖出部分底仓、锁定利润」"
                       "／2026-01-13「先卖一半」）" % (lv["upper"], (cur / cost - 1) * 100)),
        })
    return out


_MKT_CACHE: Dict[str, Any] = {"at": 0.0, "value": None}


def market_top(force: bool = False) -> Optional[Dict[str, Any]]:
    """**大盘是否处于「顶部阶段」**（他 2025-05-13 那句的前提）。

    口径：上证指数收盘在近 `WOLF_BOLL_MID_MKT_WIN`（默认 120）个交易日**区间的分位**
    ≥ `WOLF_BOLL_MID_MKT_Q`（默认 0.8，即上 20%）。
    回测证据见模块头注释（加此前提前提下"跌破中轨"才预示未来 5 日更弱）。
    取数失败 → None（由调用方决定放行与否，默认**不放行**，即宁可不卖）。
    """
    import time
    now = time.time()
    if not force and _MKT_CACHE["value"] is not None and now - _MKT_CACHE["at"] < 600:
        return _MKT_CACHE["value"]
    try:
        import datetime as _dt
        from app.services.t_backtest_data import _fetch_tushare_index_daily
        win = _env_i("WOLF_BOLL_MID_MKT_WIN", 120)
        end = _dt.date.today().strftime("%Y%m%d")
        start = (_dt.date.today() - _dt.timedelta(days=int(win * 1.7) + 30)).strftime("%Y%m%d")
        bars = _fetch_tushare_index_daily("000001.SH", start, end) or []
        bars = sorted(bars, key=lambda b: str(b.get("trade_date") or ""))
        closes = [float(b["close"]) for b in bars if b.get("close")]
        if len(closes) < win:
            return None
        seg = closes[-win:]
        lo, hi = min(seg), max(seg)
        if hi <= lo:
            return None
        pos = (closes[-1] - lo) / (hi - lo)
        q = _env_f("WOLF_BOLL_MID_MKT_Q", 0.8)
        v = {"pos": round(pos, 3), "thr": q, "top": bool(pos >= q),
             "lo": round(lo, 2), "hi": round(hi, 2), "close": round(closes[-1], 2), "win": win}
        _MKT_CACHE.update({"at": now, "value": v})
        return v
    except Exception as e:
        print(f"[boll] 大盘位置判定失败: {type(e).__name__}: {str(e)[:70]}")
        return None


def mid_break_sells(portfolio: Any, quotes: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """中轨侧：**放量、收盘跌破** 日线 BOLL 中轨 → 「完全止盈」（他 2025-05-13）。

    三个前提**都来自他的原话**：
      · 「**放量**跌破」→ 当日量 ≥ 前一交易日量 × `WOLF_BOLL_MID_VOL`（默认 1.0；尾盘调用时当日量已接近全天，可比）
      · 「跌破**收盘**」→ 由调用方限定在**收盘确认窗**（t_monitor `_in_close_window`，默认 ≥14:55）
      · 「**止盈**」→ 要求浮盈 > 0（亏损侧由既有六层止损负责，避免与止损叠加成双杀）
    `WOLF_BOLL_MID_EXIT=1`（默认 0）才被调用方纳入执行。
    """
    out: List[Dict[str, Any]] = []
    # ⚠️ 他的原话前提「顶部阶段」——**没有它这条规则不成立**（回测见模块头注释）
    mk = market_top()
    if not mk or not mk.get("top"):
        return out
    pos = portfolio
    if isinstance(pos, str):
        try:
            import json
            pos = json.loads(pos)
        except Exception:
            pos = None
    if not isinstance(pos, dict):
        return out
    q = quotes or {}
    for p in (pos.get("positions") or []):
        sym = str(p.get("symbol") or "").upper()
        if not sym:
            continue
        qt = q.get(sym) or {}
        cur = float(qt.get("current") or 0)
        if cur <= 0:
            continue
        cost = float(p.get("avg_cost") or p.get("avg_price") or 0)
        if cost > 0 and cur <= cost:
            continue                       # 「止盈」语义 → 无浮盈不在此处动作
        lv = levels(sym)
        if not lv:
            continue
        if cur >= float(lv["mid"]):
            continue
        # 「放量」：当日量 vs 前一交易日量（尾盘调用，当日量已接近全天）
        vol_min = _env_f("WOLF_BOLL_MID_VOL", 1.0)
        tv = float(qt.get("vol") or 0)
        pv = float(lv.get("prev_vol") or 0)
        vol_ratio = round(tv / pv, 3) if (tv > 0 and pv > 0) else None
        if vol_ratio is not None and vol_ratio < vol_min:
            continue
        out.append({"symbol": sym, "price": cur, "boll_mid": lv["mid"],
                    "vol_ratio": vol_ratio,
                    "reason": ("大盘顶部阶段(近%d日区间分位%.2f) + 放量(%.2f×)收盘跌破日线BOLL中轨(%.2f) "
                               "→ 完全止盈（狼大 2025-05-13「**顶部阶段**…全止盈的位置就放在日线BOLL中轨附近，"
                               "放量跌破收盘完全止盈」）" % (mk.get("win", 120), mk.get("pos", 0),
                                                       vol_ratio or 0, lv["mid"]))})
    return out


def directive(portfolio: Any = None, quotes: Optional[Dict[str, Any]] = None) -> str:
    """给纪律上下文的提示（上轨可减半 / 中轨跌破可全止盈）+ 未持有的不列。"""
    if not enabled():
        return ""
    r = active_sells(portfolio, quotes)
    mids = mid_break_sells(portfolio, quotes) if mid_exit_enabled() else []
    lines = []
    if r.get("active_sells"):
        lines.append("📉 BOLL 上轨（狼大 2026-04-29「触及上方大级别压力位(如BOLL上轨)时，逢高卖出部分底仓、"
                     "锁定利润」）：" + "；".join(s["reason"] for s in r["active_sells"][:3]))
    if mids:
        lines.append("📉 BOLL 中轨跌破（2025-05-13「放量跌破收盘完全止盈」，收盘确认）："
                     + "；".join(s["reason"] for s in mids[:3]))
    return "\n".join(lines)
