# -*- coding: utf-8 -*-
"""yellow_white.py — 指数分时黄白线状态（纯函数）

口径（狼大语料）：
  · 黄线 ≈ 中证1000（小盘/微盘/题材/不加权）
  · 白线 ≈ 上证50（大票/权重/金融/加权）
  · 黄线在上 → 小票/题材强，允许做 T/加仓
  · 白线在上 → 权重护盘/小票弱，没有买点/不追/减仓
  · 黄线快速下穿白线 + 放量 → 停止加仓、逢高卖

本模块只做“状态判定”，不做交易；输入是两只指数当日的 5min bars。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd


def _ret_at(bars: Optional[pd.DataFrame], hm: str, prev_close: float) -> Optional[float]:
    if bars is None or len(bars) == 0 or not prev_close or prev_close <= 0:
        return None
    x = bars[bars["hm"] <= hm]
    if x.empty:
        return None
    return (float(x.iloc[-1]["close"]) / float(prev_close) - 1.0) * 100.0


def _spread_at(y_bars, w_bars, hm: str, prev_y: float, prev_w: float) -> Optional[float]:
    y = _ret_at(y_bars, hm, prev_y)
    w = _ret_at(w_bars, hm, prev_w)
    if y is None or w is None:
        return None
    return y - w


def state_at(y_bars: pd.DataFrame, w_bars: pd.DataFrame, hm: str,
             prev_y: float, prev_w: float, threshold: float = 0.2) -> dict:
    """返回 {spread, y_ret, w_ret, state, slope_30m, cross, cross_down, cross_up}。

    spread = 黄线累计涨幅 - 白线累计涨幅（百分点）。
    state: yellow / white / flat。
    slope_30m: 当前 spread - 30 分钟前 spread（6 根 5min）。
    cross_down: 30 分钟前 spread > 0 且当前 <= 0（黄线下穿白线）。
    """
    y = _ret_at(y_bars, hm, prev_y)
    w = _ret_at(w_bars, hm, prev_w)
    if y is None or w is None:
        return {"state": "unknown", "spread": None, "y_ret": y, "w_ret": w,
                "slope_30m": None, "cross": None, "cross_down": False, "cross_up": False}
    spread = y - w
    state = "yellow" if spread >= threshold else ("white" if spread <= -threshold else "flat")

    # 30 分钟前（约 6 根）
    ybars = y_bars[y_bars["hm"] <= hm].reset_index(drop=True)
    wbars = w_bars[w_bars["hm"] <= hm].reset_index(drop=True)
    slope = None
    cross = None
    cross_down = cross_up = False
    if len(ybars) > 6 and len(wbars) > 6:
        hm_prev = str(ybars.iloc[-7]["hm"])
        prev_spread = _spread_at(y_bars, w_bars, hm_prev, prev_y, prev_w)
        if prev_spread is not None:
            slope = spread - prev_spread
            if prev_spread > 0 and spread <= 0:
                cross = "down"; cross_down = True
            elif prev_spread < 0 and spread >= 0:
                cross = "up"; cross_up = True
    return {"state": state, "spread": round(spread, 3), "y_ret": round(y, 3), "w_ret": round(w, 3),
            "slope_30m": round(slope, 3) if slope is not None else None,
            "cross": cross, "cross_down": cross_down, "cross_up": cross_up}


def apply_rule(state: dict, mode: str = "allow_yellow") -> bool:
    """把状态翻译成“是否允许开新仓/做 T”。

    allow_yellow（默认，语料口径）：
      · yellow → True
      · flat   → True（不禁止，但不加码）
      · white  → False
    block_white_cross（更严）：
      · white 或 cross_down → False
    """
    s = state.get("state")
    if mode == "allow_yellow":
        return s != "white"
    if mode == "block_white_cross":
        return s != "white" and not state.get("cross_down")
    return True


def state_from_spread(spread: float, threshold: float = 0.2) -> str:
    """日频/阶段级黄白线状态（本地全 A 等权黄线 - 000001 白线）。

    spread 单位：百分点（黄线 N 日收益 - 白线 N 日收益）。
      spread <= -threshold → white（白线持续强，禁止新开/减 T）
      spread >= +threshold → yellow（黄线强，允许做 T/加仓）
      其余 → flat
    """
    if spread is None:
        return "unknown"
    try:
        x = float(spread)
    except Exception:
        return "unknown"
    if x <= -abs(float(threshold)):
        return "white"
    if x >= abs(float(threshold)):
        return "yellow"
    return "flat"


def allow_from_spread(spread: float, threshold: float = 0.2) -> bool:
    """语料口径：白线持续强（spread <= -threshold）→ 不允许开新仓/加 T；其余允许。"""
    return state_from_spread(spread, threshold) != "white"
