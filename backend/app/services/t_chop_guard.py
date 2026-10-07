# -*- coding: utf-8 -*-
"""调整期/阴跌"中段"风控（狼大 2026-01 语料，2026-09-18 用户"落进去"）。

为什么需要（实测空档）：现有风控都打**极端**——破位止损（指数跌破 MA60/144/200 两根）、
derisk A（跌破关口+反抽失败）、derisk B（指数未破位但组合杀跌），而**指数阴跌中段**
（既不破位、也不单日急跌）两条都不触发 ⇒ 0126–0129 四天 −3%，期间还在执行低吸买入。

语料（docs/wolf-daily-log-xls2026.md，2026-01 当月，正是回测窗口）：
  · 01-27 (:277,:283-284)「这段时间**仓位尽量控制收盘 60%-70%**；持仓方向不变，
    **跌多了买一点、上去了卖一点**，不激进、不追高，平稳度过大手管控期；一旦选择方向向上突破，全手摁进去」
  · 01-27 (:291)「调整期的操作顺序…**不要在没有突破重回上涨趋势的时候把本来已经减出去的仓位加进去**」
  · 汇总表 (:2840,:2858)「调整期以控仓为主」「**收盘收破 5 日线降一档（60%→50%），跌破 60 日线大减**」；
    减仓动作＝「先减冲得多的、偏离大的卖一半」
  · 01-14 (:89,:93-94)「今天会**减到 55%-60% 仓位，直到过本周股指交割日**」（预设区间/事件减仓）
  · 01-26 (:259,:271)「指数没破位就没有悲观的理由…持仓等轮动，**绝对不敢横跳**」；
    「**亏损最大化**＝早上不断下跌的过程中**不断切换股票**」

三条规则（开关 WOLF_CHOP_GUARD，库内默认 0；回测驱动 setdefault=1）：
  ① 收盘仓位区间闸：收盘前把总仓压到区间上限内（先减浮盈最多/偏离最大的）；
  ② 反加仓闸：指数未重回上涨趋势时，加仓腿一律不批；仓位低于区间下限时只允许补到下限；
  ③ 降档阶梯：指数收盘破 MA5 → 区间降一档（60%→50%）；跌破 MA60 → 大减。
纯函数 + 一个取数入口，便于单测；取数失败一律 fail-open。
"""
from __future__ import annotations

import csv
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


REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
INDEX_CSV = os.getenv("WOLF_INDEX_CSV",
                      os.path.join(REPO, "data", "指数数据", "index_daily", "000001.SH.csv"))

CHOP_ON = str(os.getenv("WOLF_CHOP_GUARD", "0")).strip().lower() in ("1", "true", "yes", "on")
BAND_LO = float(os.getenv("WOLF_CHOP_BAND_LO", "0.60"))
BAND_HI = float(os.getenv("WOLF_CHOP_BAND_HI", "0.70"))
DOWNGRADE_MA5_HI = float(os.getenv("WOLF_CHOP_MA5_HI", "0.50"))
DOWNGRADE_MA5_LO = float(os.getenv("WOLF_CHOP_MA5_LO", "0.40"))
DOWNGRADE_MA60_HI = float(os.getenv("WOLF_CHOP_MA60_HI", "0.30"))
DOWNGRADE_MA60_LO = float(os.getenv("WOLF_CHOP_MA60_LO", "0.20"))

_CACHE: Dict[str, List[Tuple[str, float]]] = {}
_STATS: Dict[str, Any] = {"eval": 0, "sell_legs": 0, "add_blocked": 0, "downgrade": 0,
                          "uptrend": None, "pos_pct": None, "band": None, "reason": "未评估"}


def enabled() -> bool:
    return CHOP_ON


def stats() -> Dict[str, Any]:
    return {"on": CHOP_ON, "band_lo": BAND_LO, "band_hi": BAND_HI, **_STATS}


def stats_reset() -> None:
    for k in ("eval", "sell_legs", "add_blocked", "downgrade"):
        _STATS[k] = 0


def _series(path: str = "") -> List[Tuple[str, float]]:
    p = path or INDEX_CSV
    if p in _CACHE:
        return _CACHE[p]
    out: List[Tuple[str, float]] = []
    try:
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    out.append((str(r["trade_date"]).replace("-", ""), float(r["close"])))
                except Exception as _e_sil1:
                    _silent_alert("t_chop_guard.py:71", _e_sil1)
                    continue
    except Exception:
        out = []
    out.sort()
    _CACHE[p] = out
    return out


def index_ctx(day: str, path: str = "") -> Dict[str, Any]:
    """指数上下文：收盘/MA5/MA20/MA60、是否站上 MA5、是否站上 MA60、是否重回上涨趋势。"""
    ser = _series(path)
    d = str(day or "").replace("-", "")
    idx = None
    for i in range(len(ser) - 1, -1, -1):
        if ser[i][0] <= d:
            idx = i
            break
    if idx is None or idx < 60:
        return {"ok": False}
    closes = [c for _, c in ser[:idx + 1]]

    def ma(n: int) -> Optional[float]:
        return round(sum(closes[-n:]) / n, 4) if len(closes) >= n else None

    close = closes[-1]
    m5, m20, m60 = ma(5), ma(20), ma(60)
    return {"ok": True, "day": ser[idx][0], "close": close, "ma5": m5, "ma20": m20, "ma60": m60,
            "above_ma5": bool(m5 is not None and close >= m5),
            "above_ma60": bool(m60 is not None and close >= m60),
            "uptrend": bool(m5 is not None and m20 is not None and close >= m5 and m5 >= m20)}


def band_for(ctx: Dict[str, Any]) -> Tuple[float, float, str]:
    """③ 降档阶梯 → (lo, hi, 说明)。"""
    if not ctx.get("ok"):
        return BAND_LO, BAND_HI, "指数数据不可用 → 基础区间"
    if not ctx.get("above_ma60"):
        return DOWNGRADE_MA60_LO, DOWNGRADE_MA60_HI, "指数收盘跌破 MA60 → 大减（语料 :2840/:2858）"
    if not ctx.get("above_ma5"):
        return DOWNGRADE_MA5_LO, DOWNGRADE_MA5_HI, "指数收盘破 MA5 → 降一档（语料 :2840/:2858）"
    return BAND_LO, BAND_HI, "指数在 MA5 上 → 常规区间 60%-70%（语料 :277/:283）"


CTRL_D20 = float(os.getenv("WOLF_CHOP_CTRL_D20", "7.0"))   # 两融 20 日增速阈值（2026-01 校准）
CTRL_HI = 0.70                                            # 管控期：语料"仓位尽量控制收盘 60%-70%"
CTRL_LO = 0.60
EVENT_DROP = float(os.getenv("WOLF_CHOP_EVENT_DROP", "0.8"))  # 单日跌幅阈值 → 事件修正
EVENT_HI = 0.40                                           # 语料 0130"减到 40% 以内"


def margin_ctx(day: str, root: str = "data/_bt_size") -> Dict[str, Any]:
    """管控期代理（语料：跟随 GJD 节奏 / 压盘目的=不让两融过快增长）：
    读该回放日沙箱里的 etf_share_flow.json（as-of）→ 两融 20 日增速。取数失败 → {ok: False}（不折减）。"""
    import json as _json
    p = os.path.join(REPO, root, day.replace("-", ""), "etf_share_flow.json")
    try:
        j = _json.load(open(p, encoding="utf-8"))
        m = j.get("margin") or {}
        return {"ok": True, "date": str(j.get("date")), "d20_pct": m.get("d20_pct"),
                "rzye_yi": round(float(m.get("rzye_now") or 0) / 1e8)}
    except Exception:
        return {"ok": False}


def band_with_modifiers(ctx: Dict[str, Any], mctx: Optional[Dict[str, Any]] = None,
                        prev_close: Optional[float] = None) -> Tuple[float, float, str]:
    """基础档位区间 → 管控期折减（两融增速）→ 事件修正（单日急跌），取最严。"""
    lo, hi, why = band_for(ctx)
    d20 = (mctx or {}).get("d20_pct") if (mctx or {}).get("ok") else None
    if d20 is not None and float(d20) >= CTRL_D20 and (hi > CTRL_HI or lo > CTRL_LO):
        hi, lo = min(hi, CTRL_HI), min(lo, CTRL_LO)
        why += "；管控期折减(两融20日+%.1f%%)→≤%.0f%%" % (float(d20), CTRL_HI * 100)
    try:
        if prev_close and ctx.get("ok") and ctx["close"] / float(prev_close) - 1 <= -EVENT_DROP / 100:
            hi, lo = min(hi, EVENT_HI), min(lo, EVENT_HI - 0.10)
            why += "；事件修正(单日 %+.2f%%)→≤%.0f%%" % ((ctx["close"] / float(prev_close) - 1) * 100,
                                                          EVENT_HI * 100)
    except Exception as _e_sil2:
        _silent_alert("t_chop_guard.py:149", _e_sil2)
    return lo, hi, why


def day_plan(day: str, pos_pct: float, positions: Optional[List[Dict[str, Any]]] = None,
             gains: Optional[Dict[str, float]] = None, snapshot_root: str = "data/_bt_size") -> Dict[str, Any]:
    """运行期一次算全：区间 + 是否上涨趋势 + 加仓是否允许 + 超上限要减的腿。"""
    ctx = index_ctx(day)
    mctx = margin_ctx(day, snapshot_root)
    prev = None
    ser = _series()
    d8 = str(day).replace("-", "")
    for i in range(len(ser) - 1, -1, -1):
        if ser[i][0] <= d8:
            prev = ser[i - 1][1] if i >= 1 else None
            break
    lo, hi, why = band_with_modifiers(ctx, mctx, prev)
    ok_add, add_why = add_allowed(pos_pct, lo, bool(ctx.get("uptrend")))
    pos_value = float(os.getenv("WOLF_CHOP_POS_VALUE", "0") or 0)   # 由调用方补：见 sell_plan 调用
    out = {"ok": bool(ctx.get("ok")), "band": [lo, hi], "why": why, "uptrend": bool(ctx.get("uptrend")),
           "add_allowed": ok_add, "add_why": add_why, "mctx": mctx,
           "index": {k: ctx.get(k) for k in ("day", "close", "ma5", "ma20", "ma60")}}
    if positions is not None and pos_value > 0:
        eq = pos_value / pos_pct if pos_pct > 0 else 0.0
        out["sell"] = sell_plan(pos_value, eq, pos_pct, hi, positions, gains or {})
    return out


def add_allowed(pos_pct: float, lo: float, uptrend: bool) -> Tuple[bool, str]:
    """② 反加仓闸（纯函数）：指数未重回上涨趋势时加仓腿不批；低于下限时只允许补到下限。"""
    if uptrend:
        return True, "指数重回上涨趋势（站上 MA5 且 MA5≥MA20）→ 允许加仓（语料：突破后全手摁进去）"
    if pos_pct < lo:
        return True, "仓位 %.1f%% < 区间下限 %.0f%% → 只允许补到下限（语料：跌多了买一点）" % (pos_pct * 100, lo * 100)
    return False, ("指数未重回上涨趋势且仓位 %.1f%% ≥ 下限 %.0f%% → 不加仓"
                   "（语料 2026-01-27：不要在没有突破重回上涨趋势的时候把本来已经减出去的仓位加进去）"
                   % (pos_pct * 100, lo * 100))


def sell_plan(pos_value: float, equity: float, pos_pct: float, hi: float,
              positions: List[Dict[str, Any]], gains: Dict[str, float]) -> Dict[str, Any]:
    """① 收盘仓位区间闸（纯函数）：超上限 → 给出要减的股数（先减浮盈最多）。"""
    if equity <= 0 or pos_value <= 0:
        return {"active": False, "reason": "无仓位/净值不可用", "sells": []}
    if pos_pct <= hi:
        return {"active": False, "reason": "仓位 %.1f%% ≤ 上限 %.0f%%" % (pos_pct * 100, hi * 100), "sells": []}
    excess = pos_value - hi * equity
    order = sorted(positions, key=lambda p: -(gains.get(p.get("symbol"), 0.0)))
    sells: List[Dict[str, Any]] = []
    for p in order:
        if excess <= 0:
            break
        px = float(p.get("price") or 0)
        vol = int(p.get("volume") or 0)
        if px <= 0 or vol <= 0:
            continue
        need = int(excess / px / 100) * 100
        if need < 100:
            need = min(vol, 100)
        need = min(need, vol)
        if need < 100:
            continue
        sells.append({"symbol": p.get("symbol"), "volume": need, "price": px,
                      "gain_pct": round(gains.get(p.get("symbol"), 0.0), 2)})
        excess -= px * need
    return {"active": bool(sells), "excess": round(excess, 2), "sells": sells,
            "reason": "仓位 %.1f%% > 上限 %.0f%% → 尾盘减 %.0f 元（先减浮盈最多）"
                      % (pos_pct * 100, hi * 100, pos_value - hi * equity)}
