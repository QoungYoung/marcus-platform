# -*- coding: utf-8 -*-
"""L3 direction watch：从 PIT 主题强度表选方向 + 阶段。

数据：data/theme_strength_daily.parquet（bars 计算，r5/r20/share5）。
成员映射来自当前 stock_pool，非历史 PIT，需在 quality 中标注。
"""
from __future__ import annotations
import json, os
from typing import Any, Dict, List
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
DATA = os.path.join(ROOT, "data")
_CACHE = None
_BREADTH = None


def _table():
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = pd.read_parquet(os.path.join(DATA, "theme_strength_daily.parquet"))
        except Exception:
            _CACHE = pd.DataFrame()
    return _CACHE


def _breadth():
    global _BREADTH
    if _BREADTH is None:
        try:
            _BREADTH = pd.read_parquet(os.path.join(DATA, "theme_breadth_daily.parquet"))
        except Exception:
            _BREADTH = pd.DataFrame()
    return _BREADTH


def _stage(r5: float, r20: float, r5_rank: int) -> str:
    if r5 > 0 and r20 > 0 and r5_rank <= 3:
        return "加速"
    if r5 > 0 and r20 > 0:
        return "启动"
    if r5 > 0 and r5_rank <= 3:
        return "反弹"          # 中期未转强、短期资金先动；他的 T/技术支撑常在这
    if r5 < 0 and r20 > 0:
        return "回调"
    return "低位/观察"


def get_directions(date8: str, top: int = 3, override: List[str] | None = None,
                   min_share5: float = 0.08, policy: str = "legacy",
                   min_r20: float = 0.0, min_breadth: float = 0.0) -> Dict[str, Any]:
    if override:
        return {"asof": date8, "quality": "explicit", "stale_days": 0,
                "directions": [{"theme": t, "stage": "explicit", "quality": "explicit"} for t in override]}
    df = _table()
    if df.empty:
        return {"asof": date8, "quality": "missing", "stale_days": None, "directions": []}
    sub = df[df.trade_date <= date8]
    if sub.empty:
        return {"asof": date8, "quality": "missing", "stale_days": None, "directions": []}
    asof = str(sub.trade_date.max())
    day = sub[sub.trade_date == asof].copy()
    if policy == "r20_pos":
        # 新政策：先过“趋势门”，再排 r20；份额只做显示，不再主导
        br = _breadth()
        if not br.empty:
            b = br[br.trade_date == asof][["theme", "breadth20", "breadth_chg5"]]
            day = day.merge(b, on="theme", how="left")
        elig = day[day.r20 > min_r20]
        if min_breadth > 0 and "breadth20" in elig.columns:
            elig = elig[elig.breadth20.fillna(0) >= min_breadth]
        rows = elig.sort_values(["r20", "r5"], ascending=[False, False]).head(top)
    else:
        # 旧政策：先看“资金份额”，再看中期强度
        rows = day[day.share5 >= min_share5].sort_values(
            ["share5", "r20_rank"], ascending=[False, True]).head(top)
        if len(rows) < top:
            extra = day[~day.theme.isin(rows.theme)].sort_values(
                ["share5", "r20_rank"], ascending=[False, True]).head(top - len(rows))
            rows = pd.concat([rows, extra], ignore_index=True)
    dirs = []
    for _, r in rows.iterrows():
        dirs.append({
            "theme": r.theme, "stage": _stage(float(r.r5), float(r.r20), int(r.r5_rank)),
            "r5": round(float(r.r5), 4), "r20": round(float(r.r20), 4),
            "share5": round(float(r.share5), 4), "r5_rank": int(r.r5_rank),
            "r20_rank": int(r.r20_rank), "share5_rank": int(r.share5_rank),
            "quality": "pit_market" if asof == date8 else "stale",
        })
    stale = None
    try:
        stale = (pd.Timestamp(date8) - pd.Timestamp(asof)).days
    except Exception:
        pass
    # 观察：新政策只留“r20<0 但短期 r5 转强”的反弹方向；旧政策按 r5 补
    core_themes = {d["theme"] for d in dirs}
    watch = []
    if policy == "r20_pos":
        wdf = day[(day.r20 <= 0) & (day.r5 > 0) & (day.r5_rank <= 3) & (~day.theme.isin(core_themes))]
        wdf = wdf.sort_values("r5_rank").head(3)
    else:
        wdf = day[~day.theme.isin(core_themes)].sort_values("r5_rank").head(3)
    for _, r in wdf.iterrows():
        watch.append({
            "theme": r.theme, "stage": _stage(float(r.r5), float(r.r20), int(r.r5_rank)),
            "r5": round(float(r.r5), 4), "r20": round(float(r.r20), 4),
            "share5": round(float(r.share5), 4), "r5_rank": int(r.r5_rank),
            "r20_rank": int(r.r20_rank), "share5_rank": int(r.share5_rank),
            "quality": "pit_market" if asof == date8 else "pit_prev_close",
            "breadth20": round(float(r["breadth20"]), 3) if "breadth20" in r and pd.notna(r["breadth20"]) else None,
        })
    if asof == date8:
        quality = "pit_market"
    elif stale is not None and stale <= 3:
        quality = "pit_prev_close"   # 上一交易日收盘数据，对下一交易日有效
    else:
        quality = "stale"
    return {"asof": asof, "quality": quality, "policy": policy,
            "stale_days": stale,
            "membership": "current_stock_pool_snapshot", "directions": dirs,
            "watch_directions": watch}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--top", type=int, default=3)
    a = ap.parse_args()
    print(json.dumps(get_directions(a.date, a.top), ensure_ascii=False, indent=1))
