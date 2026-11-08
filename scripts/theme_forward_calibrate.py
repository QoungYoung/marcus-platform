#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_forward_calibrate.py — P2.5 前向校准（只读）。

判例是“未来方向”，不是当天涨幅；这里算判例主题在 T+5 的
相对上证超额，和机械 top3 的 T+5 超额做对比。
"""
from __future__ import annotations
import json, os, sys
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "jobs"))
from mainline_common import index_close, pct, trade_dates  # noqa: E402
from core.tushare_relay import relay_query  # noqa: E402

THEME = os.path.join(ROOT, "config", "mainline", "generated", "universe_theme.yaml")
CASES = {
    "20260508": {"storage"},
    "20260702": {"storage", "semi_material"},
    "20260902": {"semi_material", "storage"},
}


def future_dates(date8: str, n: int = 5):
    from datetime import datetime, timedelta
    end = (datetime.strptime(date8, "%Y%m%d") + timedelta(days=30)).strftime("%Y%m%d")
    cal = relay_query("trade_cal", exchange="SSE", start_date=date8, end_date=end, is_open="1")
    ds = sorted(str(x) for x in cal["cal_date"])
    if not ds:
        return None
    return ds[min(n - 1, len(ds) - 1)]


def src_ret(theme: dict, start: str, end: str):
    src = (theme.get("sources") or [{}])[0]
    typ, code = src.get("type"), src.get("code")
    if not typ or not code:
        return 0.0
    try:
        c = index_close(typ, code, start, end)
        if len(c) < 2:
            return 0.0
        return pct(float(c.iloc[-1]), float(c.iloc[0]))
    except Exception:
        return 0.0


def main() -> None:
    cfg = yaml.safe_load(open(THEME, encoding="utf-8"))
    themes = {t["id"]: t for t in (cfg.get("themes") or []) + (cfg.get("satellite_themes") or [])}
    print("date | expected(theme:T+5 excess) | mechanical top3(T+5 excess)")
    print("---|---|---")
    for date, expected in CASES.items():
        end = future_dates(date, 5)
        if not end:
            print(f"{date} | N/A | N/A"); continue
        bench = index_close("csi", "000001.SH", date, end)
        bret = pct(float(bench.iloc[-1]), float(bench.iloc[0])) if len(bench) >= 2 else 0.0
        exp = []
        for tid in sorted(expected):
            t = themes.get(tid, {})
            exp.append(f"{tid}:{(src_ret(t, date, end) - bret):+.1%}")
        top = []
        path = os.path.join(ROOT, "data", "mainline", date, "theme.json")
        if os.path.exists(path):
            j = json.load(open(path, encoding="utf-8"))
            for r in (j.get("trial") or []) + (j.get("focus") or []):
                t = themes.get(r["id"], {})
                top.append(f"{r['id']}:{(src_ret(t, date, end) - bret):+.1%}")
        print(f"{date} | {', '.join(exp)} | {', '.join(top[:3])}")


if __name__ == "__main__":
    main()
