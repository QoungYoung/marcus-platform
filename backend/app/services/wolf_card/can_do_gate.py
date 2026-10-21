# -*- coding: utf-8 -*-
"""低位超额线：哪天可以做、哪天不能做。

来自 08-10/08-12 原话：
  · 08-10：指数第一次测试上方高点压力，冲高回落是正常走法；券商/科技不涨，只拉银行/微小题材，
    “今天什么都不买。就是等”；“没什么意思的时候 不买 但凡买就是被套”。
  · 08-12：小跳给出低点、量能缩转放；“科技横盘”“小B调了三天”，才做低位超额。

规则（组合闸，不是单因子）：
  · 指数不在“首次测试压力位”；
  · 指数/科技有回踩/小B，而不是冲高；
  · 至少 2 个低位方向有资金/广度改善；
  · 半导体不是单边下跌（弱反弹/横盘）。
"""
from __future__ import annotations
import json, os
from typing import Any, Dict
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
DATA = os.path.join(ROOT, "data")
_C = {}


def _index():
    if "idx" not in _C:
        rows = json.load(open(os.path.join(DATA, "index_daily_000001_SH.json"), encoding="utf-8"))["rows"]
        df = pd.DataFrame(rows, columns=["trade_date", "close", "pre_close", "amount"])
        _C["idx"] = df.sort_values("trade_date").reset_index(drop=True)
    return _C["idx"]


def index_state(date8: str) -> Dict[str, Any]:
    df = _index()
    i = df.index[df.trade_date == date8]
    if len(i) == 0:
        d = df[df.trade_date <= date8].tail(1)
        if len(d) == 0: return {"date": date8, "error": "no_index"}
        i = [d.index[0]]
    i = int(i[0])
    close = float(df.iloc[i].close)
    w20 = df.iloc[max(0, i - 19):i + 1]
    w60 = df.iloc[max(0, i - 59):i + 1]
    high20 = float(w20.close.max()); high60 = float(w60.close.max())
    r5 = close / float(df.iloc[i - 5].close) - 1 if i >= 5 else None
    amt5 = float(df.iloc[max(0, i - 4):i + 1].amount.mean())
    amt20 = float(w20.amount.mean())
    vol_ratio = amt5 / amt20 if amt20 else None
    first_test = close >= high20 * 0.997 and (r5 or 0) > 0
    pullback = close < high20 * 0.995
    return {"date": date8, "close": close, "high20": high20, "high60": high60,
            "r5": round(r5, 4) if r5 is not None else None,
            "vol5_20": round(vol_ratio, 3) if vol_ratio else None,
            "first_test": bool(first_test), "pullback": bool(pullback)}


def market_breadth(date8: str) -> Dict[str, Any]:
    """全市场涨跌家数/中位涨幅/量能（收盘口径；盘中 14:55 近似）。"""
    if "mkt" not in _C:
        import pandas as _pd
        b = _pd.read_parquet(os.path.join(DATA, "bars_combined.parquet"),
                             columns=["ts_code", "trade_date", "close", "amount"])
        piv = b.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
        amt = b.groupby("trade_date").amount.sum()
        _C["mkt"] = (piv, amt)
    piv, amt = _C["mkt"]
    if date8 not in piv.index:
        return {"date": date8, "error": "no_date"}
    i = piv.index.get_loc(date8)
    ret = piv.iloc[i] / piv.iloc[i - 1] - 1
    up, dn = int((ret > 0).sum()), int((ret < 0).sum())
    a5 = float(amt.iloc[max(0, i - 4):i + 1].mean())
    a20 = float(amt.iloc[max(0, i - 19):i + 1].mean())
    return {"date": date8, "up": up, "down": dn, "ratio": round(up / dn, 3) if dn else None,
            "median_pct": round(float(ret.median()) * 100, 3),
            "limit_up": int((ret >= 0.095).sum()), "limit_down": int((ret <= -0.095).sum()),
            "amt5_20": round(a5 / a20, 3) if a20 else None}


def can_do(date8: str, min_top_score: float = 0.015, min_gap: float = 0.02) -> Dict[str, Any]:
    try:
        try:
            from .rotation_gate import rotation_state
        except ImportError:
            from rotation_gate import rotation_state
        st = rotation_state(date8)
    except Exception as e:
        return {"date": date8, "can_do": False, "reason": "rotation_error:%s" % (str(e)[:80],)}
    ix = index_state(date8)
    low = st.get("low_directions") or []
    improving = [x for x in low if (x.get("share5_chg5") or 0) > 0 or (x.get("breadth_chg5") or 0) > 0.05]
    top = low[0]["score"] if low else None
    # 用 top1-top4 的分散度，而不是 top1-top2：四个低位方向分数常常很接近
    fourth = low[3]["score"] if len(low) > 3 else (low[-1]["score"] if low else None)
    gap = (top - fourth) if top is not None and fourth is not None else None
    reasons = []
    if ix.get("first_test"):
        reasons.append("index_first_test_resistance")
    if not ix.get("pullback"):
        reasons.append("index_not_pullback")
    if len(improving) < 2:
        reasons.append("low_directions_improving<2")
    if not st.get("mainline_sideways", st.get("tech_sideways")):
        reasons.append("mainline_not_sideways")
    if top is None or top < min_top_score:
        reasons.append("direction_score_too_low")
    if gap is None or gap < min_gap:
        reasons.append("direction_not_dispersed")
    return {"date": date8, "can_do": len(reasons) == 0, "reasons": reasons,
            "index": ix, "tech_sideways": st.get("tech_sideways"),
            "direction_top": round(top, 4) if top is not None else None,
            "direction_gap": round(gap, 4) if gap is not None else None,
            "mainline": st.get("mainline"), "mainline_r20": st.get("mainline_r20"),
            "mainline_share20": st.get("mainline_share20"),
            "mainline_sideways": st.get("mainline_sideways"),
            "improving_dirs": [x["theme"] for x in improving],
            "low_directions": low}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    a = ap.parse_args()
    print(json.dumps(can_do(a.date), ensure_ascii=False, indent=1))
