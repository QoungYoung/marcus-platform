# -*- coding: utf-8 -*-
"""科技横盘 + 资金切低位的状态识别。

输入：theme_strength_daily / theme_breadth_daily / regime_gate。
输出：tech_sideways 布尔、低位方向分数、建议观察方向。
"""
from __future__ import annotations
import os, sys
from typing import Any, Dict, List
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))), "backend"))
try:
    from .regime_gate import build_gate
except ImportError:
    from regime_gate import build_gate

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
DATA = os.path.join(ROOT, "data")
LOW = ["医药", "电力/公用", "军工/航天", "AI/算力/科技"]
_ML_CACHE = {}
_C = {}


def _load(name):
    if name not in _C:
        _C[name] = pd.read_parquet(os.path.join(DATA, name))
    return _C[name]


def _row(df, theme, date8):
    x = df[(df.theme == theme) & (df.trade_date <= date8)].sort_values("trade_date").tail(6)
    return x


def _mainline(date8: str, strength) -> tuple:
    """当月/当日主线：优先 main_line_state 日期快照，缺失时用成交额份额最大主题兜底。"""
    if date8 in _ML_CACHE:
        return _ML_CACHE[date8]
    import json
    cand = [
        os.path.join(DATA, "main_line_state_%s.json" % date8),
        os.path.join(DATA, "main_line_state_%s-%s-%s.json" % (date8[:4], date8[4:6], date8[6:8])),
    ]
    # 资金池口径：用**20 日平均 share5**，避免单日份额跳变把主线切来切去。
    # main_line_state 的 mainline 是选股主线（按 r5），不一定是资金来源。
    try:
        hist = strength[strength.trade_date <= date8].sort_values("trade_date")
        sc = []
        for th, g in hist.groupby("theme"):
            g = g.tail(20)
            if len(g) >= 10:
                sc.append((th, float(g.share5.mean())))
        if sc:
            sc.sort(key=lambda x: -x[1])
            _ML_CACHE[date8] = (str(sc[0][0]), "share5_20d")
            return _ML_CACHE[date8]
    except Exception:
        pass
    _ML_CACHE[date8] = (None, "missing")
    return _ML_CACHE[date8]


def rotation_state(date8: str) -> Dict[str, Any]:
    s, b = _load("theme_strength_daily.parquet"), _load("theme_breadth_daily.parquet")
    g = build_gate(date8)
    mainline, ml_src = _mainline(date8, s)
    ml = _row(s, mainline, date8) if mainline else s.iloc[0:0]
    ml_r20 = float(ml.iloc[-1].r20) if len(ml) else None
    ml_share_chg = float(ml.iloc[-1].share5 - ml.iloc[0].share5) if len(ml) >= 6 else None
    ml_share20 = float(ml.share5.mean()) if len(ml) else None
    mainline_sideways = bool(ml_r20 is not None and abs(ml_r20) <= 0.03
                             and (ml_share_chg is None or ml_share_chg < 0)
                             and (g.get("operation") in ("side", "defense", None)))
    # 兼容旧调用
    tech_sideways = mainline_sideways
    dirs = []
    for th in LOW:
        if mainline and th == mainline:
            continue
        x = _row(s, th, date8)
        y = b[(b.theme == th) & (b.trade_date <= date8)].sort_values("trade_date").tail(1)
        if not len(x): continue
        share_chg = float(x.iloc[-1].share5 - x.iloc[0].share5) if len(x) >= 6 else 0.0
        bchg = float(y.iloc[-1].breadth_chg5) if len(y) and pd.notna(y.iloc[-1].breadth_chg5) else 0.0
        lu = int(y.iloc[-1].limit_up) if len(y) else 0
        dirs.append({"theme": th, "r20": round(float(x.iloc[-1].r20), 4),
                     "r5": round(float(x.iloc[-1].r5), 4),
                     "share5_chg5": round(share_chg, 4),
                     "breadth_chg5": round(bchg, 4), "limit_up": lu,
                     "score": round(0.5 * share_chg + 0.3 * bchg + 0.2 * (lu / 100.0), 4)})
    dirs.sort(key=lambda x: -x["score"])
    return {"date": date8, "tech_sideways": tech_sideways,
            "mainline_sideways": mainline_sideways,
            "mainline": mainline, "mainline_source": ml_src,
            "mainline_r20": round(ml_r20, 4) if ml_r20 is not None else None,
            "mainline_share_chg5": round(ml_share_chg, 4) if ml_share_chg is not None else None,
            "mainline_share20": round(ml_share20, 4) if ml_share20 is not None else None,
            "regime": {k: g.get(k) for k in ("regime", "operation", "allow_new_base", "confirm_needed", "allow_t", "no_trade")},
            "semi": {"r20": round(float(_row(s, "半导体/芯片", date8).iloc[-1].r20), 4) if len(_row(s, "半导体/芯片", date8)) else None,
                     "share5_chg5": (round(float(_row(s, "半导体/芯片", date8).iloc[-1].share5 - _row(s, "半导体/芯片", date8).iloc[0].share5), 4)
                                     if len(_row(s, "半导体/芯片", date8)) >= 6 else None)},
            "low_directions": dirs}


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    a = ap.parse_args()
    print(json.dumps(rotation_state(a.date), ensure_ascii=False, indent=1))
