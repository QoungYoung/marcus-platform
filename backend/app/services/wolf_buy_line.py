# -*- coding: utf-8 -*-
"""买单「挂到低位的线」上（狼大 2026-04-10 原话；2026-09-20 用户拍板 C）。

**语料依据（唯一一条，逐字）**
  2026-04-10「我教你们个方法 卖出去的怕忍不住买回来怎么 **所有自己手上的票找低位的线挂进去**
              不管是**黄金分割** 还是**均线** **挂远一点** 然后看盘也行 聊天…」
旁证他用挂单：2026-04-01「我的国算**上下挂单** 做得很嗨皮」；2026-03-27「然后**挂单** 他不拉我就不做」。

**为什么改**：我们此前的 `suggest_bid_price = 现价×0.999`（`t_monitor.py:519`）**无语料依据**
（代码里字段名自认 `slippage_budget: 0.001`），而且方向是**对我们有利**——实测 0106 五笔买单里
三笔成交价正好等于 `现价×0.999`，等于每次买入白拿 0.1% 的便宜（回测偏乐观）。
按他的话改成"把限价挂在现价下方的技术线上，价格下来碰到线才成交"。

**自设（语料没给，全部标注待校）**
  · 线集合 `WOLF_BUY_LINE_SET`（默认 `ma10,ma20,prev_low,fib618`）：他只说"黄金分割/均线"，
    没说哪几条 ⇒ 取 MA10/MA20 + 昨低 + 红K 的 0.618 进场位（后者有语料：2026-03-05「只用0.382和0.618」、
    03-06「用第一根或者前两根顶板红K的开盘价和收盘价做指标计算进场位置」）。
  · 最小间距 `WOLF_BUY_LINE_MIN_GAP_PCT`（默认 0.5%）：把"挂远一点"量化（**自设待校**）。
  · 选线：取现价下方**离现价最近**的那条线（"低位的线"里最高的那条）。
  · 成交价 = **线价**（保守：不假设跌得更深时能拿到更好的价）。

**语义**：`resolve()` 只在「当日最低已经碰到线」（`day_low ≤ 线价`）时返回 fire=True；
碰不到 ⇒ 不发单（挂单等待，下一轮再看）——正是他"挂单、他不拉我就不做"的做法。
开关 `WOLF_BUY_LINE` 库内默认 **0**（生产零影响），回测 pins 置 1。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple


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


LINE_ENV = "WOLF_BUY_LINE"
MIN_GAP_ENV = "WOLF_BUY_LINE_MIN_GAP_PCT"
SET_ENV = "WOLF_BUY_LINE_SET"
_DEFAULT_SET = ("ma10", "ma20", "prev_low", "fib618")
_LINE_NAMES = {"ma5": "MA5", "ma10": "MA10", "ma20": "MA20", "ma60": "MA60",
               "prev_low": "昨低", "fib618": "红K0.618"}

_CACHE: Dict[Tuple[str, str, int], Dict[str, float]] = {}


def enabled() -> bool:
    return str(os.getenv(LINE_ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


def scope() -> str:
    """挂线规则的**适用范围**（`WOLF_BUY_LINE_SCOPE`，默认 `all`）：

    · `all`   —— 所有日内低吸买单都挂线（2026-09-20 首版口径）；
    · `rebuy` —— **只对"再买"挂线**（买入时已持有该票 ⇒ 加仓/回补/正T买回），首次建仓走原低吸口径。
                 依据：语料 2026-04-10 那句的上下文是「**卖出去的怕忍不住买回来**」= 再买纪律；
    · `new`   —— 反过来：**只对首次建仓挂线**，再买走原口径。

    ⚠️ 离线实测（y26 真实买过的 166 笔，见台账）：被 C 挡掉的 87 笔里，**再买 48 笔（含 12 只 10 日 ≥+10% 的大牛）**、
    首次建仓 39 笔（10 日均值 −0.68%、中位 −2.92%、仅 33% 上涨）⇒ **`rebuy` 会继续挡住大牛、只放回较差那批**，
    与"把盈利票放回来"的目标相反；按同一份证据，`new` 才是放回大牛的那一侧。选择权在口径侧。
    """
    return str(os.getenv("WOLF_BUY_LINE_SCOPE", "all")).strip().lower() or "all"


def applies(sc: str = None, held: bool = False) -> bool:
    """该买单是否受挂线规则约束（纯函数，便于单测）。`held` 未知时按 True 处理（=继续挂线，保守）。"""
    s = str(sc if sc is not None else scope()).strip().lower()
    if s == "rebuy":
        return bool(held)
    if s == "new":
        return not bool(held)
    return True


def min_gap_pct() -> float:
    try:
        return max(0.0, float(os.getenv(MIN_GAP_ENV, "0.5")))
    except Exception:
        return 0.5


def tol_pct() -> float:
    """「此刻在线上」的容差（自设待校，默认 0.3%）：现价 ≤ 线价×(1+容差) 即视为挂单成交。"""
    try:
        return max(0.0, float(os.getenv("WOLF_BUY_LINE_TOL_PCT", "0.3")))
    except Exception:
        return 0.3


def line_set() -> List[str]:
    raw = str(os.getenv(SET_ENV) or "").strip()
    items = [x.strip().lower() for x in raw.split(",") if x.strip()] if raw else list(_DEFAULT_SET)
    return [x for x in items if x in _LINE_NAMES]


def label(name: str) -> str:
    return _LINE_NAMES.get(str(name), str(name))


def _norm(bars) -> List[Dict[str, float]]:
    """容错归一化：接受 [{'close','high','low'}…] / {'20260105': {...}} / 元组列表（历史踩过形状坑）。"""
    out: List[Dict[str, float]] = []
    seq = bars
    if isinstance(bars, dict):
        seq = [bars[k] for k in sorted(bars)]
    for b in (seq or []):
        try:
            if isinstance(b, dict):
                d = str(b.get("date") or "")
                out.append({"date": d, "close": float(b.get("close") or 0), "high": float(b.get("high") or 0),
                            "low": float(b.get("low") or 0), "open": float(b.get("open") or b.get("close") or 0)})
            elif isinstance(b, (list, tuple)) and len(b) >= 5:      # (date, open, high, low, close)
                out.append({"date": str(b[0]), "close": float(b[4]), "high": float(b[2]), "low": float(b[3]),
                            "open": float(b[1])})
        except Exception as _e_sil1:
            _silent_alert("wolf_buy_line.py:111", _e_sil1)
            continue
    return out


def _ma(bars: List[Dict[str, float]], n: int) -> Optional[float]:
    if len(bars) < n:
        return None
    seg = [b["close"] for b in bars[-n:] if b["close"] > 0]
    return round(sum(seg) / len(seg), 3) if len(seg) == n else None


def _fib618(bars: List[Dict[str, float]], symbol: str, day: str) -> Optional[float]:
    """红K 的 0.618 进场位（复用 `wolf_redk_setup.detect`，拿不到就 None）。"""
    try:
        from app.services import wolf_redk_setup as _rk
        seq = [(str(b.get("date") or day), float(b.get("open") or b["close"]), b["high"], b["low"], b["close"])
               for b in bars]
        r = _rk.detect(seq, str(day), str(symbol))
        if r and float(r.get("entry") or 0) > 0:
            return round(float(r["entry"]), 3)
    except Exception as _e_sil2:
        _silent_alert("wolf_buy_line.py:132", _e_sil2)
    return None


def bars_sqlite(symbol: str, day: str, n: int = 40):
    """回测专用日线源：`data/_bt_full/bars.sqlite`（**强制 trade_date < day**，无未来函数）。

    为什么需要它（2026-09-20 实测三个源都不行）：
      · `t_monitor._daily_dated` 在回放里**恒定停在 seed cut（20251231）**，与回放日无关 ⇒ MA 算错；
      · `t_monitor._prev_daily` 常常 0–1 天 ⇒ MA10/MA20 恒缺（线集合退化成"昨低"）；
      · as-of 网关的 `daily_bars_for` 返回的 bar **没有日期**且数值对不上（实测 002364 给 31–35，实际 26–27）。
    ⇒ 回测里直接用 bars.sqlite（该库是回放的权威日线源）。生产（无该库/未设 env）自动返回 None，
      由调用方回落到生产链路，**生产零影响**。路径可用 `WOLF_BUY_LINE_BARS_DB` 覆盖。
    """
    p = os.getenv("WOLF_BUY_LINE_BARS_DB") or ""
    if not p:
        cand = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))), "data", "_bt_full", "bars.sqlite")
        p = cand if os.path.exists(cand) else ""
    if not p or not os.path.exists(p):
        return None
    try:
        import sqlite3
        s = str(symbol).upper()
        code = ("%s.%s" % (s[2:], s[:2])) if (len(s) >= 8 and s[:2] in ("SH", "SZ", "BJ")) else s
        con = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
        try:
            rows = con.execute("""select trade_date,open,high,low,close from bars where ts_code=? and trade_date<?
                                  order by trade_date desc limit ?""", (code, str(day), int(n))).fetchall()
        finally:
            con.close()
        rows = list(reversed(rows))
        out = [{"date": str(r[0]), "open": float(r[1]), "high": float(r[2]),
                "low": float(r[3]), "close": float(r[4])} for r in rows]
        return out or None
    except Exception:
        return None


def lines(bars, symbol: str, day: str) -> Dict[str, float]:
    """当日可用的「低位的线」（全部由 **as-of 昨日** 的日线算出，日内不变）。"""
    key = (str(symbol), str(day), len(bars or []))
    if key in _CACHE:
        return _CACHE[key]
    b = _norm(bars)
    # ⚠️ 防未来函数：只用到**当日之前**的日线（有的数源会把当日那根未收盘的 bar 也带上）。
    try:
        b = [x for x in b if (not x.get("date")) or str(x["date"]) < str(day)]
    except Exception as _e_sil3:
        _silent_alert("wolf_buy_line.py:181", _e_sil3)
    if not b:
        _CACHE[key] = {}
        return {}
    got: Dict[str, float] = {}
    for name in line_set():
        try:
            if name.startswith("ma"):
                v = _ma(b, int(name[2:]))
            elif name == "prev_low":
                v = round(b[-1]["low"], 3) if (b and b[-1]["low"] > 0) else None
            elif name == "fib618":
                v = _fib618(b, symbol, day)
            else:
                v = None
            if v and v > 0:
                got[name] = float(v)
        except Exception as _e_sil4:
            _silent_alert("wolf_buy_line.py:199", _e_sil4)
            continue
    _CACHE[key] = got
    return got


def pick(current: float, lns: Dict[str, float], gap_pct: Optional[float] = None) -> Tuple[Optional[str], Optional[float]]:
    """现价下方（至少 min_gap）里**最高**的那条线；没有合格线 ⇒ (None, None)（当日不挂单）。"""
    try:
        cur = float(current or 0)
        if cur <= 0:
            return None, None
        g = min_gap_pct() if gap_pct is None else float(gap_pct)
        ceil = cur * (1.0 - max(0.0, g) / 100.0)
        ok = [(n, float(v)) for n, v in (lns or {}).items() if 0 < float(v) <= ceil]
        if not ok:
            return None, None
        n, v = max(ok, key=lambda x: x[1])
        return n, round(v, 3)
    except Exception:
        return None, None


def resolve(bars, symbol: str, day: str, quote) -> Dict[str, Any]:
    """买单决策：返回 {fire, line, price, why}。

    fire=True  ⇔ 当日最低已碰到线（`day_low ≤ 线价`）⇒ 限价单在线上成交，成交价 = 线价。
    fire=False ⇒ 挂单等待（调用方**不要**写触发、**不要**记 done，下一轮再评估）。
    """
    cur = low = 0.0
    try:
        cur = float((quote or {}).get("current") or 0)
        low = float((quote or {}).get("low") or 0)
    except Exception as _e_sil5:
        _silent_alert("wolf_buy_line.py:232", _e_sil5)
    lns = lines(bars, symbol, day)
    if not lns:
        return {"fire": False, "line": None, "price": None, "why": "无可用的低位线"}
    # 2026-09-20 修（首版锚错）：线必须**锚在昨收**（挂单是"下单一处、挂一天等它来"），
    #   首版用**实时现价**作参考 ⇒ 价格越跌、被选中的线越往下挪 ⇒ 实测 0106 一笔都没成交
    #   （601138 的线从 MA10 63.27 退到昨低 62.71，而当日低 62.86 永远追不上）。
    #   语料「找低位的线挂进去」= 事先选好线挂上去 ⇒ 锚点应是与时间无关的昨收（拿不到才退回现价）。
    _b = _norm(bars)
    ref = 0.0
    try:
        ref = float(_b[-1]["close"]) if (_b and _b[-1]["close"] > 0) else 0.0
    except Exception:
        ref = 0.0
    if ref <= 0:
        ref = cur if cur > 0 else (low or 0)
    name, px = pick(ref, lns)
    if not name or not px:
        return {"fire": False, "line": None, "price": None,
                "why": "现价 %.3f 下方 %.2f%% 内没有线（挂单等待）：%s"
                       % (ref, min_gap_pct(), "/".join("%s=%.3f" % (label(k), v) for k, v in sorted(lns.items())))}
    # 2026-09-20 修（实跑暴露）：首版用「**当日最低**碰过线」判成交 ⇒ 会在**现价已远离线**时才发单
    #   （实测 0106 09:55：线 MA10=63.267，当日低 62.90 早已碰过，但现价已 64.64 ⇒ AI 按
    #   「现价较建议买价高 1.21% > 1% 脱节」拒绝执行，触发变 await_retry）。
    #   真限价单是"**此刻价格就在线上**"才成交 ⇒ 改为判 `现价 ≤ 线价 ×(1+TOL)`。
    _tol = tol_pct()
    if cur > 0 and cur <= px * (1.0 + _tol / 100.0):
        # 成交价 = **min(线价, 现价)**：限价单不会比线价更贵；若价格已经在线下方，
        #   就按现价成交（更便宜，也更贴近"我们是在这一刻才发现"的现实）。
        # 2026-09-20 修：首版一律取线价 ⇒ 当现价已在线下方时，"建议买价"高于市价，
        #   被 AI 的「现价与建议价脱节 >1%」判据拒单（实测 0106 SZ002463：线 MA5=74.426、
        #   现价 73.020 ⇒ 触发后 `decided/wait`，白丢一笔）。
        _fill = round(min(px, cur), 3)
        return {"fire": True, "line": name, "price": _fill, "line_price": px,
                "why": "%s=%.3f 现价 %.3f 已在线上（容差 %.2f%%，成交 %.3f，当日低 %.3f）"
                       % (label(name), px, cur, _tol, _fill, low)}
    return {"fire": False, "line": name, "price": px,
            "why": "%s=%.3f 等价格下来（现价 %.3f 高于线价 %.2f%%，当日低 %.3f）"
                   % (label(name), px, cur, (cur / px - 1.0) * 100 if px else 0.0, low)}
