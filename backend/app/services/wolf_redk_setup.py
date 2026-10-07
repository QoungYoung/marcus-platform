# -*- coding: utf-8 -*-
"""wolf_redk_setup.py — 狼大「**盘整后第一根大红K**」形态 + 由它推出的**止损锚**。

## 语料（逐字，全部有出处）
- 2026-03-06：「1 **板块长期盘整第一根大红K**，观察里面涨停或者大涨的个股，想追这种方向的
  用**第一根或者前两根顶板红K的开盘价和收盘价**做指标计算**进场位置和止损位置**」
- 2025-06-26：「个股的话到**前两根红K的0.618位置**就值得大仓位了」
- 2026-06-15：「突破下跌趋势**第一根开头我加一半** 突破后的**第二根红K尾盘 打满**…
  中间会有调仓 但是没有减仓」
- 2026-03-05：「黄金分割**只用0.382和0.618**」「按我**0.618买入**的情况下 这样止损就是**-6%左右**」
  +「13日内跌破**波段低点的-3%**没有收回 直接止损」
- 「**顶板**」= 打板/涨停（2021-06-16「直接顶板买 不然买不进」；2025-10-29「20CM你们也敢顶板」）

## 锚的推导（两条语料交叉）
止损 = 波段低点×0.97；买点 = 该波段的 0.618 回撤位；两者相差 −6%
⇒ **波段低点 = 0.94/0.97 × 买点 ≈ 买点下方 3.1%** ⇒ 与"红K 的**开盘价**"（红K低端）吻合：
   实体≈8% 的红K、买点取 0.618 ⇒ 止损距买点 −5.88% ≈ 他说的 −6% ✓
⇒ 本模块的 `big_red_open()` 就是**给 ①波段逻辑止损提供锚**（替代原先自设的"建仓前13根最低"）。

## 自设参数（语料未给，全部可 env 覆盖，标注**待校**）
- `WOLF_REDK_RISE_PCT=9.5`     大红K 门槛（主板；"顶板"≈涨停）
- `WOLF_REDK_RISE_PCT_20=19.5` 20CM 板门槛
- `WOLF_REDK_LOOKBACK=10`      锚回看：买点（含）之前多少个交易日内的红K才算"本轮信号"
- `WOLF_REDK_CONSOL_DAYS=20` / `WOLF_REDK_CONSOL_AMP=25`  "长期盘整"定义（振幅%，**自设待校**：
  实测 603773 需放宽到 ≤25% 才认得出 0305 那个信号）
开关：本模块纯函数，是否启用由调用方开关决定（`WOLF_EARLY_STOP_ANCHOR=redk`）。
"""
from __future__ import annotations
import os
from typing import Any, Dict, List, Optional


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


RISE_DEFAULT = 9.5
RISE20_DEFAULT = 19.5
LOOKBACK_DEFAULT = 10
CONSOL_DAYS_DEFAULT = 20
CONSOL_AMP_DEFAULT = 25.0


def _env_f(name: str, dflt: float) -> float:
    try:
        return float(os.getenv(name, str(dflt)) or dflt)
    except Exception:
        return dflt


def _env_i(name: str, dflt: int) -> int:
    try:
        return int(float(os.getenv(name, str(dflt)) or dflt))
    except Exception:
        return dflt


def rise_threshold(symbol: Any = "") -> float:
    """按板别取"大红K/顶板"门槛：创业板 300/301 与科创 688/689 用 20CM 门槛。"""
    s = str(symbol or "").upper()
    code = s[2:5] if len(s) >= 5 and s[:2] in ("SH", "SZ", "BJ") else s[:3]
    if code in ("300", "301", "688", "689"):
        return _env_f("WOLF_REDK_RISE_PCT_20", RISE20_DEFAULT)
    return _env_f("WOLF_REDK_RISE_PCT", RISE_DEFAULT)


def _rows(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """规整成 [{'date','open','high','low','close'}]（按日期升序，丢无效行）。"""
    out = []
    for b in bars or []:
        if not isinstance(b, dict):
            continue
        d = str(b.get("date") or b.get("trade_date") or "").replace("-", "")[:8]
        try:
            o, h, l, c = (float(b.get("open") or 0), float(b.get("high") or 0),
                          float(b.get("low") or 0), float(b.get("close") or 0))
        except Exception as _e_sil1:
            _silent_alert("wolf_redk_setup.py:73", _e_sil1)
            continue
        if len(d) == 8 and c > 0:
            out.append({"date": d, "open": o, "high": h, "low": l, "close": c})
    out.sort(key=lambda x: x["date"])
    return out


def is_big_red(prev_close: float, row: Dict[str, Any], symbol: Any = "") -> bool:
    """红K（收≥开）且涨幅 ≥ 门槛（"顶板"级）。"""
    if not prev_close or prev_close <= 0:
        return False
    if row["close"] < row["open"]:
        return False
    return (row["close"] / prev_close - 1) * 100 >= rise_threshold(symbol)


def big_red_open(bars: List[Dict[str, Any]], day: Any, symbol: Any = "",
                 lookback: Optional[int] = None) -> Optional[float]:
    """**买点（含）之前** lookback 个交易日内、最近一根大红K 的**开盘价**（= 波段低点锚）。

    语料依据见模块头："用第一根或者前两根顶板红K的**开盘价**和收盘价做指标计算…**止损位置**"。
    找不到（窗口内没有大红K / 数据不足）→ None（调用方 fail-open 退回原锚）。
    """
    rows = _rows(bars)
    if len(rows) < 2:
        return None
    d = str(day or "").replace("-", "")[:8]
    idx = [i for i, r in enumerate(rows) if not d or r["date"] <= d]
    if not idx:
        return None
    i = idx[-1]
    n = int(lookback if lookback is not None else _env_i("WOLF_REDK_LOOKBACK", LOOKBACK_DEFAULT))
    if n <= 0:
        return None
    for j in range(i, max(-1, i - n), -1):        # 从最近往前找
        if j - 1 < 0:
            break
        if is_big_red(rows[j - 1]["close"], rows[j], symbol):
            return float(rows[j]["open"] or 0) or None
    return None


def detect(bars: List[Dict[str, Any]], day: Any, symbol: Any = "",
           consol_days: Optional[int] = None, consol_amp: Optional[float] = None,
           first_lookback: int = 5) -> Optional[Dict[str, Any]]:
    """**当日**是否为「盘整后第一根大红K」信号 → dict（含 0.618 进场位/止损位/前高），否则 None。

    自设部分：`consol_*`（"长期盘整"的量化）与 `first_lookback`（"第一根"的量化）——语料未给，待校。
    """
    rows = _rows(bars)
    d = str(day or "").replace("-", "")[:8]
    idx = [i for i, r in enumerate(rows) if r["date"] == d]
    if not idx:
        return None
    i = idx[-1]
    if i < 1:
        return None
    row, pc = rows[i], rows[i - 1]["close"]
    if not is_big_red(pc, row, symbol):
        return None
    cd = int(consol_days if consol_days is not None else _env_i("WOLF_REDK_CONSOL_DAYS", CONSOL_DAYS_DEFAULT))
    if i - cd < 0:
        return None
    seg = rows[i - cd:i]
    lo = min(r["low"] for r in seg if r["low"] > 0) if any(r["low"] > 0 for r in seg) else 0
    hi = max(r["high"] for r in seg)
    if lo <= 0:
        return None
    amp = _env_f("WOLF_REDK_CONSOL_AMP", CONSOL_AMP_DEFAULT) if consol_amp is None else float(consol_amp)
    if (hi - lo) / lo * 100 > amp:
        return None
    for k in range(max(1, i - first_lookback), i):       # "第一根"：近 first_lookback 日无同级大红K
        if k - 1 >= 0 and is_big_red(rows[k - 1]["close"], rows[k], symbol):
            return None
    o, c = row["open"], row["close"]
    if c <= o:
        return None
    return {"date": d, "open": o, "close": c,
            "entry": c - 0.618 * (c - o),        # 0.618 回撤位（进场）
            "entry_382": c - 0.382 * (c - o),    # 0.382 位（备选）
            "stop": o * 0.97,                    # 波段低点(=红K开盘价) −3%
            "prev_high": max(r["high"] for r in rows[max(0, i - 13):i]) if i > 0 else None}
