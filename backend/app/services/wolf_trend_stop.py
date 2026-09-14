# -*- coding: utf-8 -*-
"""趋势线法（狼大止损六层之**② 成趋势后**）—— 纯函数，不直接下单。

────────────────────────────────────────────────────────────────
狼大原话（逐字，`docs/PRODUCTION_PIPELINE.md` §241 已核；**参数是有的**）:
  · 2026-03-06「**已经成为趋势后**…这个就没意义了 更多应该转为我之前说的趋势波段止盈止损方法
    也就是用**趋势线**的方法…**不是一个策略用到底的**」← 划定了本条与 ① 的分界
  · 2021-01-28「我主要做波段…**用 13日 34日做强弱分类，60日是我的底线**，
    甚至**没站稳 34日带量下穿的我都会砍掉**」
  · 2016-03-30「趋势线用 **13 和 34 天**来标」
  · 2016-08-05「除非**跌破趋势线**或者大盘崩」
  · 2026-01-12「收黑K**跌破5日线**…**减仓**避一下」← 减仓，不是清仓
  · 2021-01-29「杀下去引发踩踏又没有指数标拖着 ＝ 破趋势 → **直接斩仓**」
⚠️ 2026-09-14 更正：本仓此前（`wolf_early_stop.py` 旧注释 / `docs/stop-layer-audit.md` 1ac8c8b）
   写"语料没给趋势线参数"是**错的**，已在该文件 aad76e1 更正。
────────────────────────────────────────────────────────────────

判据（本模块口径，逐条挂原话）:
  1. **收盘 < MA60**                         → `exit`   （"60日是我的底线"）
  2. **收盘 < MA34 且 带量**                  → `exit`   （"没站稳34日带量下穿的我都会砍掉"）
  3. **黑K 且 收盘 < MA5**                    → `reduce` （"收黑K跌破5日线…减仓避一下"）
  4. 其它                                    → `hold`
  另：**13/34 强弱分类**（MA13 ≥ MA34 = strong，否则 weak）随 `state` 一起返回，
     并只在开关 `WOLF_TREND_WEAK_NO_VOL=1` 时改变动作（弱分类下破 34 不要求带量也算 exit）——
     默认 0 = 严格按字面（破 34 必须带量）。

唯一**自造**的东西 = "带量"的量化：`WOLF_TREND_VOL_RATIO`（默认 1.2 × 近 20 日**成交额**均量）
  —— 语料只给了"带量"两个字、没给数字，故此处**显式标注为代理**，可配、默认保守；
  换标的口径（如 5 日均量比、换手率）只需改这一个函数。

数据不足（< MA60+20 根）→ `hold`（fail-open，不凭缺失数据卖票，与 ① 同口径）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

DEFAULTS = {"ma_short": 5, "ma_fast": 13, "ma_slow": 34, "ma_floor": 60, "vol_win": 20}


def _f(name: str, dflt: float) -> float:
    try:
        return float(os.getenv(name, str(dflt)))
    except (TypeError, ValueError):
        return dflt


def _i(name: str, dflt: int) -> int:
    try:
        return int(float(os.getenv(name, str(dflt))))
    except (TypeError, ValueError):
        return dflt


def enabled() -> bool:
    """`WOLF_TREND_STOP=0` 关闭（回退到既有 stop_loss_price 口径）。"""
    return os.getenv("WOLF_TREND_STOP", "1").strip() not in ("0", "false", "no")


def vol_ratio_threshold() -> float:
    """「带量」阈值（**代理**，语料无数值；默认 1.2 倍近 20 日成交额均量）。"""
    return _f("WOLF_TREND_VOL_RATIO", 1.2)


def weak_without_vol() -> bool:
    """弱分类（MA13<MA34）时，破 34 日线是否**免带量**也算 exit。默认 0 = 严格字面。"""
    return os.getenv("WOLF_TREND_WEAK_NO_VOL", "0").strip() not in ("0", "false", "no")


def _norm(r: Any):
    """归一化一根日K → (date, open, high, low, close, amount, vol)。

    生产 `TMonitor._daily_dated` 给的是 **dict**（keys: date/close/high/low/vol，**无 open、无 amount**）；
    `eval_leg_metrics.Bars/ReplayBars` 给的是 **tuple** (date, open, high, low, close, amount)。
    两种都要吃 —— ⚠️ 2026-09-14 踩过：只按 tuple 写，生产传 dict 直接 KeyError，
    被外层 except 吞掉 → "②趋势线判定异常(跳过)" → **静默失效**（与本仓第 8 例同类）。
    """
    if isinstance(r, dict):
        return (str(r.get("date") or ""), float(r.get("open") or 0), float(r.get("high") or 0),
                float(r.get("low") or 0), float(r.get("close") or 0),
                float(r.get("amount") or 0), float(r.get("vol") or 0))
    seq = list(r) + [0] * 7
    return (str(seq[0]), float(seq[1] or 0), float(seq[2] or 0), float(seq[3] or 0),
            float(seq[4] or 0), float(seq[5] or 0), float(seq[6] or 0))


def fresh_only() -> bool:
    """只对**新破位**自动执行（默认 1 = 分阶段上线）。

    为什么需要：规则上线那天，存量持仓里可能**早就**跌破 60 日线（只是以前没有这条规则），
    若直接执行 = 一次"补跌式清仓"，那是**上线时点造成的伪信号**，不是他的信号。
    打开时：久已破位（近 `WOLF_TREND_FRESH_DAYS` 个交易日从未站上该线）→ 只**提示**不自动卖。
    要完全忠实原话（久破位也砍）→ `WOLF_TREND_FRESH_ONLY=0`。
    """
    return os.getenv("WOLF_TREND_FRESH_ONLY", "1").strip() not in ("0", "false", "no")


def fresh_days() -> int:
    try:
        return max(int(float(os.getenv("WOLF_TREND_FRESH_DAYS", "5"))), 1)
    except (TypeError, ValueError):
        return 5


def _ma(vals: List[float], n: int) -> Optional[float]:
    if n <= 0 or len(vals) < n:
        return None
    seg = vals[-n:]
    return sum(seg) / float(n)


def state(bars: List[Any], cfg: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """从**日线**序列算趋势线状态。

    bars: 升序，元素为 (date8, open, high, low, close, amount)（`t_monitor._daily_dated` 与
    `eval_leg_metrics.Bars` 同构；缺 open 的降级数据源把 open 置 0 亦可）。
    返回 dict：{ok, n, close, prev_close, ma5/13/34/60, cls, is_black, vol_ratio,
              below_ma5/34/60, vol_break34, black_break5, note}
    """
    c = dict(DEFAULTS)
    c.update(cfg or {})
    need = int(c["ma_floor"]) + int(c["vol_win"]) // 2
    rows = [_norm(r) for r in (bars or []) if r]
    if len(rows) < need:
        return {"ok": False, "n": len(rows), "note": "日线不足 %d 根（需 MA60+量能窗口）" % need}
    closes = [r[4] for r in rows if r[4] > 0]
    if not closes:
        return {"ok": False, "n": len(rows), "note": "无有效收盘价"}
    opens = [r[1] for r in rows]
    # 量能：优先成交额（tuple 源）；缺失（生产 _daily_dated 只有 vol）→ 用成交量
    amts = [r[5] for r in rows]
    if sum(a for a in amts if a > 0) <= 0:
        amts = [r[6] for r in rows]
    close = closes[-1]
    prev = closes[-2] if len(closes) > 1 else close
    ma5 = _ma(closes, int(c["ma_short"]))
    ma13 = _ma(closes, int(c["ma_fast"]))
    ma34 = _ma(closes, int(c["ma_slow"]))
    ma60 = _ma(closes, int(c["ma_floor"]))
    open_last = opens[-1] if opens else 0.0
    # 黑K：有 open 就用 close<open；缺 open（降级数据源）→ 用 close<prev_close 代理
    is_black = (close < open_last) if open_last > 0 else (close < prev)
    vol_win = int(c["vol_win"])
    vbase = [a for a in amts[-vol_win - 1:-1] if a > 0]
    vol_ratio = (amts[-1] / (sum(vbase) / len(vbase))) if (vbase and amts and amts[-1] > 0) else None
    _vol_src = "amount" if any(r[5] > 0 for r in rows) else "vol"
    # 近 fresh_days 个交易日里是否有过"站在线上"——用于区分"新破位"与"久已破位"
    def _was_above(win: int, days: int) -> bool:
        for back in range(1, days + 1):
            seg = closes[:len(closes) - back + 1] if back > 1 else closes
            m = _ma(seg, win)
            if m and seg and seg[-1] > m:
                return True
        return False
    _fd = fresh_days()
    cls = None
    if ma13 is not None and ma34 is not None:
        cls = "strong" if ma13 >= ma34 else "weak"
    out = {"ok": True, "n": len(rows), "close": round(close, 4), "prev_close": round(prev, 4),
           "ma5": ma5, "ma13": ma13, "ma34": ma34, "ma60": ma60,
           "cls": cls, "is_black": bool(is_black), "vol_ratio": round(vol_ratio, 3) if vol_ratio else None,
           "below_ma5": bool(ma5 and close < ma5), "below_ma34": bool(ma34 and close < ma34),
           "below_ma60": bool(ma60 and close < ma60), "vol_src": _vol_src, "note": ""}
    thr = vol_ratio_threshold()
    out["vol_break34"] = bool(out["below_ma34"] and (vol_ratio is not None and vol_ratio >= thr))
    out["vol_break34_weak"] = bool(out["below_ma34"] and cls == "weak")
    out["black_break5"] = bool(out["below_ma5"] and is_black)
    out["fresh60"] = bool(out["below_ma60"] and _was_above(int(c["ma_floor"]), _fd))
    out["fresh34"] = bool(out["below_ma34"] and _was_above(int(c["ma_slow"]), _fd))
    return out


def decision(st: Dict[str, Any]) -> Tuple[str, str]:
    """按原话给动作 → (action, why)；action ∈ {"hold","reduce","exit"}。"""
    if not st or not st.get("ok"):
        return ("hold", "趋势线数据不足（fail-open 不动作）")
    if st.get("below_ma60"):
        if fresh_only() and not st.get("fresh60"):
            return ("notice", "**久已**跌破 60 日线（近 %d 日从未站上）→ 只提示不自动卖"
                              "（分阶段上线：WOLF_TREND_FRESH_ONLY=1；要完全忠实原话设 0）" % fresh_days())
        return ("exit", "收盘跌破 60 日线（狼大 2021-01-28「60日是我的底线」）")
    if st.get("vol_break34"):
        if fresh_only() and not st.get("fresh34"):
            return ("notice", "**久已**跌破 34 日线（近 %d 日从未站上）→ 只提示不自动卖"
                              "（WOLF_TREND_FRESH_ONLY=1）" % fresh_days())
        return ("exit", "没站稳 34 日线**带量**下穿（量比 %.2fx ≥ %.2fx，代理口径）"
                % (st.get("vol_ratio") or 0, vol_ratio_threshold()))
    if st.get("below_ma34") and weak_without_vol() and st.get("cls") == "weak" and (st.get("fresh34") or not fresh_only()):
        return ("exit", "弱分类（MA13<MA34）跌破 34 日线（WOLF_TREND_WEAK_NO_VOL=1）")
    if st.get("black_break5"):
        return ("reduce", "收黑 K 跌破 5 日线 → 减仓避一下（狼大 2026-01-12）")
    return ("hold", "在趋势线之上（MA13 %s MA34）" % ("≥" if st.get("cls") == "strong" else "<"))


def evaluate(bars: List[Any], cfg: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """一步到位：bars → {action, why, line, state}。line = 触发所用的那条线（hold 时为 None）。"""
    st = state(bars, cfg)
    act, why = decision(st)
    line = None
    if act == "notice":
        line = st.get("ma60") if st.get("below_ma60") else st.get("ma34")
    if act == "exit":
        line = st.get("ma60") if st.get("below_ma60") else st.get("ma34")
    elif act == "reduce":
        line = st.get("ma5")
    return {"action": act, "why": why, "line": line, "state": st}
