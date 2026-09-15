# -*- coding: utf-8 -*-
"""wolf_entry_filters.py — 建仓腿的**两道真闸门**（2026-09-15，用户指示：真对接生产，不做只记录影子）。

闸门 1 · `WOLF_CLOSE_POS_GATE`（默认 **1**）：**距前低最远 ∧ 半强收盘 → 不买**
  证据（总账 §50）：`dist_prevlow 高档 ∧ pos∈[0.5,0.8)` 这一交叉格 −2.270%（块状 t −3.14），
  H1 −1.514（t −3.19）/ H2 −3.034（t −2.19）两段一致；同档其它腿不显著（−0.808/t −0.30）。
  · 「高档」不写死数值：按**当日候选池**的 dist_prevlow 三分位动态取（避免自设阈值）；
  · `pos = (close − low) / (high − low)`（当日收盘在当日区间的位置），`[0.5, 0.8)` 来自证据。

闸门 2 · `WOLF_DEFENSIVE_GATE`（默认 **1**）：**低位 ∧ 相对大盘不弱**（他的"抗跌"）
  他的话：2021-01-27「什么叫防御型，就是**大盘跌的时候他少跌一点**」（比较基准=大盘，有原话）；
  2025-10-23「防御类品种，就是那种跌了也就最多跌 2 个点的」（特征描述）；2026-04-07「低位横盘多时…」。
  · 「低位」= dist_prevlow 处于**当日候选池下三分位**（与闸门 1 同一分位口径，动态，不写死）；
  · 「不弱」= 近 `WOLF_DEF_REL_WIN`（**默认 20，自设**）交易日个股涨幅 ≥ 同期指数涨幅（相对超额 ≥0）；
  · ⚠️ 窗口 20 日是**自设**（他没给窗口），可 env 调；阈值"相对超额 ≥0"直接来自"少跌一点"的语义。

两道闸门都只作用于**新开买腿**；失败/数据缺失一律**放行**（fail-open，绝不因为算不出而停买）。
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

MA_LINE_MIN_CLOSES = 144          # 与 rotation_switch_arm 一致：算得出 144 线所需的最少 K 线
POS_LO, POS_HI = 0.5, 0.8         # 闸门 1 的 pos 区间（来自 §50 的交叉格）


def close_pos_enabled() -> bool:
    return os.getenv("WOLF_CLOSE_POS_GATE", "1").strip().lower() not in ("0", "false", "no")


def defensive_enabled() -> bool:
    return os.getenv("WOLF_DEFENSIVE_GATE", "1").strip().lower() not in ("0", "false", "no")


def def_rel_win() -> int:
    try:
        return max(int(os.getenv("WOLF_DEF_REL_WIN", "20")), 2)
    except Exception:
        return 20


# ── 纯函数（可单测）──────────────────────────────────────────────
def tercile_high(values: Sequence[float]) -> Optional[float]:
    """该组数值的**上三分位下限**（≥ 它算"高档"）；样本 <6 个返回 None（不判）。"""
    v = sorted(float(x) for x in values if x is not None)
    if len(v) < 6:
        return None
    k = int(len(v) * 2 / 3)
    return v[min(k, len(v) - 1)]


def tercile_low(values: Sequence[float]) -> Optional[float]:
    """该组数值的**下三分位上界**（≤ 它算"低档"）；样本 <6 个返回 None。"""
    v = sorted(float(x) for x in values if x is not None)
    if len(v) < 6:
        return None
    k = int(len(v) / 3)
    return v[min(k, len(v) - 1)]


def pos_of(close: float, high: float, low: float) -> Optional[float]:
    if high is None or low is None or close is None or high <= low:
        return None
    return (float(close) - float(low)) / (float(high) - float(low))


def close_pos_blocked(dist_prevlow: Optional[float], pos: Optional[float],
                      pool: Sequence[float]) -> Tuple[bool, str]:
    """闸门 1：距前低高档 ∧ pos∈[0.5,0.8) → 拦。"""
    if dist_prevlow is None or pos is None:
        return False, "数据缺失 → 放行"
    cut = tercile_high(pool)
    if cut is None:
        return False, "候选池样本<6 → 不判（放行）"
    if dist_prevlow >= cut and POS_LO <= pos < POS_HI:
        return True, ("距前低高档(%.2f≥%.2f) ∧ 半强收盘(pos=%.2f∈[%.1f,%.1f)) → 不买（§50 交叉格）"
                      % (dist_prevlow, cut, pos, POS_LO, POS_HI))
    return False, "未命中交叉格"


def defensive_blocked(dist_prevlow: Optional[float], ret_stock: Optional[float],
                      ret_index: Optional[float], pool: Sequence[float]) -> Tuple[bool, str]:
    """闸门 2：低位（dist 低档）∧ 近 N 日弱于大盘 → 拦（他的"抗跌"）。"""
    if dist_prevlow is None or ret_stock is None or ret_index is None:
        return False, "数据缺失 → 放行"
    cut = tercile_low(pool)
    if cut is None:
        return False, "候选池样本<6 → 不判（放行）"
    if dist_prevlow <= cut and ret_stock < ret_index:
        return True, ("低位(dist=%.2f≤%.2f) ∧ 近%d日弱于大盘(个股%.2f%% < 指数%.2f%%) → 不买（他的抗跌）"
                      % (dist_prevlow, cut, def_rel_win(), ret_stock, ret_index))
    return False, "未命中抗跌格"


# ── 取数与批量过滤（供 rotation_switch_arm 调用）──────────────────
def leg_features(symbol: str, as_of: str) -> Dict[str, Any]:
    """→ {dist_prevlow, pos, ret20, close, prev_low}（拉 540 天日线；失败返回 {}）。"""
    try:
        import datetime as _dt
        _p = os.path.dirname(os.path.abspath(__file__))
        if _p not in sys.path:
            sys.path.insert(0, _p)
        import wolf_confirm_pick as _WCP
        s = str(symbol)
        ts = (s[2:] + "." + s[:2]) if s[:2] in ("SH", "SZ") else s
        start = (_dt.date.today() - _dt.timedelta(days=540)).strftime("%Y%m%d")
        rows = _WCP.gz("daily", {"ts_code": ts, "start_date": start, "end_date": as_of},
                       "ts_code,trade_date,close,low,amount,high") or []
        rows = sorted(rows, key=lambda x: str(x[1]))
        if len(rows) < MA_LINE_MIN_CLOSES:
            return {}
        closes = [float(x[2]) for x in rows]
        lows = [float(x[3]) for x in rows]
        highs = [float(x[5]) for x in rows]
        prev_low = lows[-2] if len(lows) >= 2 else None
        dist = ((closes[-1] / prev_low - 1) * 100.0) if prev_low else None
        win = def_rel_win()
        ret = ((closes[-1] / closes[-1 - win] - 1) * 100.0) if len(closes) > win else None
        return {"dist_prevlow": dist, "pos": pos_of(closes[-1], highs[-1], lows[-1]),
                "ret20": ret, "close": closes[-1], "prev_low": prev_low}
    except Exception as e:
        print("ENTRY_FILTER_FEAT_ERR", symbol, str(e)[:70], file=sys.stderr)
        return {}


def index_ret(win: int, as_of: str) -> Optional[float]:
    """同期指数涨幅（000001.SH）。"""
    try:
        _p = os.path.dirname(os.path.abspath(__file__))
        if _p not in sys.path:
            sys.path.insert(0, _p)
        import wolf_ma144_regime as _M
        rows = _M.closes(refresh=False) or []
        seq = [c for d, c in rows if str(d) <= str(as_of)]
        if len(seq) > win:
            return (seq[-1] / seq[-1 - win] - 1) * 100.0
    except Exception as e:
        print("INDEX_RET_ERR", str(e)[:60], file=sys.stderr)
    return None


def filter_legs(legs: List[Dict[str, Any]], as_of: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """对**新开买腿**施加两道闸门 → (保留, 被拦[{symbol, why}])。数据缺失一律放行。"""
    if not legs:
        return legs, []
    on1, on2 = close_pos_enabled(), defensive_enabled()
    if not (on1 or on2):
        return legs, []
    feats, pool = {}, []
    for b in legs:
        f = leg_features(b.get("symbol"), as_of)
        feats[b.get("symbol")] = f
        if f.get("dist_prevlow") is not None:
            pool.append(f["dist_prevlow"])
    idx = index_ret(def_rel_win(), as_of) if on2 else None
    kept, blocked = [], []
    for b in legs:
        f = feats.get(b.get("symbol")) or {}
        why = None
        if on1:
            hit, w1 = close_pos_blocked(f.get("dist_prevlow"), f.get("pos"), pool)
            if hit:
                why = w1
        if why is None and on2:
            hit, w2 = defensive_blocked(f.get("dist_prevlow"), f.get("ret20"), idx, pool)
            if hit:
                why = w2
        if why:
            blocked.append({"symbol": b.get("symbol"), "why": why})
        else:
            kept.append(b)
    return kept, blocked
