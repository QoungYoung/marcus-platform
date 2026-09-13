# -*- coding: utf-8 -*-
"""t_range_today.py — 拉 SH588170 今天(20260907) 5min, 算 T 区间/触发。

2026-09-13: 数据源由 gzcloud 代理改为 promax 中继（stk_mins 走 pcd.mobcvb.cn，见 core/tushare_relay.py）。
"""
import os, sys, json, time


def _relay():
    """加载 core/tushare_relay.py（datahubco + promax，替代已失效的 gzcloud 代理）。"""
    import importlib, pathlib
    try:
        return importlib.import_module("tushare_relay")
    except ImportError:
        pass
    for p in pathlib.Path(__file__).resolve().parents:
        if (p / "core" / "tushare_relay.py").exists():
            sys.path.insert(0, str(p / "core"))
            return importlib.import_module("tushare_relay")
    raise ImportError("core/tushare_relay.py 未找到")


def stk_mins(ts, freq, trade_date):
    """分钟线：promax `stk_mins`（tushare 兼容字段 trade_time/open/close/high/low/vol）。"""
    _fields, items = _relay().relay_items(
        "stk_mins", ts_code=ts, freq=freq, trade_date=trade_date,
        fields="trade_time,open,close,high,low,vol")
    return items or []


bars = stk_mins("588170.SH", "5min", "20260907")
print("today 588170.SH 5min bars:", len(bars))
if not bars:
    print("今天(09-07)无数据(可能未开盘/数据未出), 回退用09-03: close=0.935 low=0.926 high=0.962")
    prev_close=0.935; prev_low=0.926; prev_high=0.962
else:
    cs=[float(b[2]) for b in bars]; hs=[float(b[3]) for b in bars]; ls=[float(b[4]) for b in bars]; vs=[float(b[5]) for b in bars]
    print("today first", bars[0][0], "last", bars[-1][0], "close", cs[-1])
    print("today high", max(hs), "low", min(ls), "close_sofar", cs[-1])
    # running vwap
    amt = sum(float(b[2])*float(b[5]) for b in bars); vol = sum(vs)
    print("today vwap(近)", round(amt/vol,4))
    prev_close=cs[-1]; prev_low=min(ls); prev_high=max(hs)
# 做T区间(狼大规则)
lowbuy_hi = round(prev_low*1.005,3); lowbuy_lo = round(min(prev_low*0.995, prev_low*1.0),3)
tclose = round(prev_close,3)
print("--- T 区间 ---")
print("低吸位(近前低/回撤): %.3f ~ %.3f" % (lowbuy_lo, lowbuy_hi))
print("T出位(+3~5点, 近今日/前高): %.3f ~ %.3f" % (round(prev_close*1.03,3), round(prev_close*1.05,3)))
print("破位走(前低*0.99): %.3f" % round(prev_low*0.99,3))
