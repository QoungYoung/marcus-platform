# -*- coding: utf-8 -*-
"""trend_break_v2.py — 突破确认 v2（纯函数）

结构位：平台/箱体、W 底颈线、60/120 前高、下降通道、20 日高 fallback。
量能：z20、vol/MA20、收盘位置、amount/MA20。
时间：连续 3 个收盘 >= level。
回踩：level/MA10 不破 + 缩量。
止损：突破日收盘（或 level×0.97）。

只做“突破确认”，不决定趋势底仓仓位；不依赖 IO，便于单测/回放。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULTS: Dict[str, Any] = {
    "breakout_mult": 1.005,
    "vol_z": 1.5,
    "vol_ma_mult": 1.5,
    "close_pos_min": 0.7,
    "amount_ma_mult": 1.5,
    "stand_closes": 3,
    "confirm_window": 5,
    "lookback": 20,
    "max_anchor_age": 10,
    "max_chase": 0.08,
    "level_min_ratio": 0.85,
    "level_max_ratio": 1.08,
    # 结构优先级：Phase 1 回放显示 prior_high 稳定为正、platform 稳定为负
    "structure_order": ("prior_high", "channel", "neckline", "platform", "fallback"),
    "allowed_structures": ("prior_high", "channel", "neckline", "platform", "fallback"),
    "pullback_window": 10,
    "pullback_tol": 0.02,
    "pullback_hold": 0.97,
    "pullback_vol": 0.8,
    "stop_buffer": 0.97,
    "box_max_height": 0.12,
    "box_min_touches": 2,
    "neckline_low_tol": 0.03,
    "neckline_min_gap": 15,
    "channel_window": 60,
}

# v2 生产/影子默认：Phase 1 回放显示 platform 稳定为负、prior_high 稳定为正
V2_PARAMS = {**DEFAULTS, "allowed_structures": ("prior_high", "channel", "neckline", "fallback")}


def _ma(v: Sequence[float], n: int, i: int) -> Optional[float]:
    if i + 1 < n:
        return None
    x = [float(v[j]) for j in range(i - n + 1, i + 1)]
    return sum(x) / n


def _z(v: Sequence[float], i: int, n: int = 20) -> Optional[float]:
    if i < n + 1:
        return None
    w = [float(v[j]) for j in range(i - n, i)]
    mu = sum(w) / n
    var = sum((x - mu) ** 2 for x in w) / n
    sd = var ** 0.5
    return 0.0 if sd <= 0 else (float(v[i]) - mu) / sd


def _local_lows(low: Sequence[float], i: int, span: int = 5, look: int = 60) -> List[Tuple[int, float]]:
    out = []
    for k in range(max(span, i - look), i):
        lo = max(0, k - span)
        hi = min(len(low), k + span + 1)
        if low[k] == min(low[lo:hi]) and low[k] <= low[k - 1] and low[k] <= low[k + 1]:
            out.append((k, float(low[k])))
    ded = []
    for k, v in out:
        if ded and k - ded[-1][0] <= 5:
            if v <= ded[-1][1]:
                ded[-1] = (k, v)
        else:
            ded.append((k, v))
    return ded


def detect_structure(high: Sequence[float], low: Sequence[float], close: Sequence[float],
                     i: Optional[int] = None, p: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """返回 {structure, level, desc}；优先级：平台/颈线 > 60/120 前高 > 下降通道 > 20 日高。"""
    if not high or not low or not close:
        return {"structure": "", "level": None, "desc": "no data"}
    p = {**DEFAULTS, **(p or {})}
    i = len(close) - 1 if i is None else int(i)
    if i < 25:
        return {"structure": "", "level": None, "desc": "too short"}
    h = [float(x) for x in high]; l = [float(x) for x in low]; c = [float(x) for x in close]
    candidates = []

    # 1) 平台/箱体（20/40/60）
    for w in (20, 40, 60):
        if i < w:
            continue
        # 结构位用“当前 bar 之前”的窗口，避免把今天的突破高点当成箱体上沿
        hh = max(h[i - w:i]); ll = min(l[i - w:i])
        if ll <= 0:
            continue
        height = (hh - ll) / ll
        if height > p["box_max_height"]:
            continue
        touches = sum(1 for k in range(i - w, i) if h[k] >= hh * 0.995)
        if touches < p["box_min_touches"]:
            continue
        if c[i] < hh * 0.97:
            continue
        recent_touch = any(h[k] >= hh * 0.995 for k in range(max(i - w, i - 10), i))
        if not recent_touch:
            continue
        candidates.append({"structure": "platform", "level": hh, "desc": "平台%d日 高%.2f 振幅%.1f%% 触碰%d" % (w, hh, height * 100, touches)})
        break

    # 2) W 底颈线
    lows = _local_lows(l, i, 5, 60)
    best_w = None
    if len(lows) >= 2:
        for a in range(len(lows) - 1):
            for b in range(a + 1, len(lows)):
                k1, v1 = lows[a]; k2, v2 = lows[b]
                gap = k2 - k1
                if gap < p["neckline_min_gap"] or gap > 35:
                    continue
                if i - k2 < 3:
                    continue
                if abs(v1 - v2) / max(v1, v2) > p["neckline_low_tol"]:
                    continue
                neck = max(h[k1:k2 + 1])
                # 颈线必须明显高于两低点；否则只是普通震荡
                if neck <= max(v1, v2) * 1.05:
                    continue
                if c[i] < neck * 0.95:
                    continue
                # 取最近的一组 W，而不是第一组
                if best_w is None or k2 > best_w[0]:
                    best_w = (k2, neck, k1)
    if best_w is not None:
        candidates.append({"structure": "neckline", "level": best_w[1],
                           "desc": "W底颈线%.2f 低点%d/%d" % (best_w[1], best_w[2], best_w[0])})

    # 3) 60/120 前高
    for w in (60, 120):
        if i >= w:
            ph = max(c[i - w:i])
            candidates.append({"structure": "prior_high", "level": ph, "desc": "%d日前高%.2f" % (w, ph)})
            break

    # 4) 下降通道
    w = int(min(p["channel_window"], i))
    if w >= 20:
        xs = list(range(w)); ys = h[i - w:i]
        mx = sum(xs) / w; my = sum(ys) / w
        den = sum((x - mx) ** 2 for x in xs)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0.0
        if slope < 0:
            upper = my + slope * (w - 1 - mx)
            candidates.append({"structure": "channel", "level": upper, "desc": "下降通道上轨%.2f 斜率%.4f" % (upper, slope)})

    # 5) 20 日高 fallback
    if i >= 20:
        candidates.append({"structure": "fallback", "level": max(c[i - 19:i]), "desc": "20日高fallback"})

    order = {k: i for i, k in enumerate(p["structure_order"])}
    allowed = set(p.get("allowed_structures") or p["structure_order"])
    if not candidates:
        return {"structure": "", "level": None, "desc": "no structure"}
    # 结构位必须“贴着当前价”：太低=很久以前的结构，太高=还没突破；两者都不是当下结构
    near = [x for x in candidates if x["structure"] in allowed and x["level"]
            and c[i] * p["level_min_ratio"] <= x["level"] <= c[i] * p["level_max_ratio"]]
    if not near:
        return {"structure": "", "level": None, "desc": "no near structure"}
    near.sort(key=lambda x: order.get(x["structure"], 9))
    return near[0]


def volume_ok(vol: Sequence[float], amount: Sequence[float], high: Sequence[float],
              low: Sequence[float], close: Sequence[float], i: int,
              p: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    p = {**DEFAULTS, **(p or {})}
    if i < 21:
        return {"ok": False, "reason": "too short"}
    z = _z(vol, i, 20)
    vma = _ma(vol, 20, i)
    ama = _ma(amount, 20, i)
    rng = float(high[i]) - float(low[i])
    cpos = (float(close[i]) - float(low[i])) / rng if rng > 0 else 0.0
    ok = (z is not None and z >= p["vol_z"]
          and vma and float(vol[i]) >= vma * p["vol_ma_mult"]
          and cpos >= p["close_pos_min"]
          and ama and float(amount[i]) >= ama * p["amount_ma_mult"])
    return {"ok": bool(ok), "z20": None if z is None else round(z, 2),
            "vol_ma": round(float(vol[i]) / vma, 2) if vma else None,
            "close_pos": round(cpos, 2),
            "amount_ma": round(float(amount[i]) / ama, 2) if ama else None,
            "reason": "" if ok else "volume/close/amount"}


def stand3(close: Sequence[float], level: float, anchor: int, p: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    p = {**DEFAULTS, **(p or {})}
    n = int(p["stand_closes"])
    if anchor + n >= len(close):
        return {"ok": False, "done": False, "n": 0}
    vals = [float(close[anchor + k]) for k in range(1, n + 1)]
    ok = all(v >= float(level) for v in vals)
    return {"ok": bool(ok), "done": True, "n": n, "closes": [round(v, 2) for v in vals]}


def pullback_ok(high: Sequence[float], low: Sequence[float], close: Sequence[float],
                vol: Sequence[float], level: float, anchor: int, i: Optional[int] = None,
                p: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    p = {**DEFAULTS, **(p or {})}
    i = len(close) - 1 if i is None else int(i)
    w = int(p["pullback_window"])
    for j in range(anchor + int(p["stand_closes"]) + 1, min(i + 1, anchor + w + 1)):
        ma10 = _ma(close, 10, j)
        touch = float(low[j]) <= float(level) * (1 + p["pullback_tol"])
        hold = float(close[j]) >= float(level) * p["pullback_hold"] and (ma10 is None or float(close[j]) >= ma10)
        shrink = float(vol[j]) <= float(vol[anchor]) * p["pullback_vol"]
        if touch and hold and shrink:
            return {"ok": True, "index": j, "desc": "回踩%.2f 缩量%.2f" % (level, float(vol[j]) / float(vol[anchor]))}
    return {"ok": False, "index": None}


def evaluate(high: Sequence[float], low: Sequence[float], close: Sequence[float],
             open_: Sequence[float], vol: Sequence[float], amount: Sequence[float],
             p: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """扫描最后一个 bar 的突破状态。

    返回 stage ∈ {"", "SETUP", "TRIGGER", "CONFIRM", "PULLBACK", "FAIL"}。
    """
    p = {**DEFAULTS, **(p or {})}
    n = len(close)
    if n < 30:
        return {"stage": "", "desc": "too short"}
    i = n - 1
    # 逐日回溯：结构位必须用“突破日之前”的数据，否则今天的突破高点会把箱体上沿抬高
    anchor = None
    st = None
    lv = None
    max_age = int(p["max_anchor_age"])
    lo = max(30, i - max_age)
    for j in range(i, lo - 1, -1):
        if j - 1 < 25:
            continue
        sj = detect_structure(high, low, close, j - 1, p)
        lj = sj.get("level")
        if not lj:
            continue
        if float(close[j]) < float(lj) * p["breakout_mult"]:
            continue
        # 不追：突破日收盘不能离结构位太远，且不能是 10 天前的老突破
        if float(close[j]) > float(lj) * (1 + p["max_chase"]):
            continue
        if volume_ok(vol, amount, high, low, close, j, p).get("ok"):
            anchor = j; st = sj; lv = float(lj)
            break
    if anchor is None:
        st = detect_structure(high, low, close, i, p)
        level = st.get("level")
        if not level:
            return {"stage": "", "desc": st.get("desc", "")}
        lv = float(level)
        if float(close[i]) < lv * 0.97:
            return {"stage": "", "level": lv, "structure": st["structure"], "desc": "below structure"}
        return {"stage": "SETUP", "level": lv, "structure": st["structure"], "desc": st.get("desc", "")}
    # 止损/证伪
    stop = max(float(close[anchor]) * p["stop_buffer"], lv * p["stop_buffer"])
    if float(close[i]) < stop:
        return {"stage": "FAIL", "level": lv, "structure": st["structure"], "anchor": anchor,
                "stop": round(stop, 2), "desc": "跌破止损"}
    s3 = stand3(close, lv, anchor, p)
    if not s3.get("done"):
        return {"stage": "TRIGGER", "level": lv, "structure": st["structure"], "anchor": anchor,
                "stop": round(stop, 2), "stand": s3, "desc": "待3连站"}
    if not s3.get("ok"):
        return {"stage": "FAIL", "level": lv, "structure": st["structure"], "anchor": anchor,
                "stop": round(stop, 2), "stand": s3, "desc": "3连站失败"}
    pb = pullback_ok(high, low, close, vol, lv, anchor, i, p)
    # 首笔统一在 CONFIRM；回踩只作为第二笔的附加标记，不再把首笔拖到回踩
    return {"stage": "CONFIRM", "level": lv, "structure": st["structure"], "anchor": anchor,
            "stop": round(stop, 2), "stand": s3,
            "pullback": pb if pb.get("ok") else None,
            "desc": "3连站确认" + ("+回踩" if pb.get("ok") else "")}
