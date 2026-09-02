# -*- coding: utf-8 -*-
"""
t_signal.py — 做T信号 code 化 (P2, 2026-09-02) — 狼大做T体系
================================================================================
狼大做T核心(语料 docs/t-trading-logic.md):
  T1 缩转放: 量能由缩转放 = T买点 ('量能开始缩转放了'8-12, '转折点是缩转放那瞬间'8-04)
  T2 黄线: 日内均线(日级近似MA5), 跌破直接走 ('绝对不能破的点就是日均线那条黄线'8-04)
  T_sell: T出条件(7-29原话): 放量反弹→第一次分时高点后停止放量→第二次拉升无量不过前高→T出
用法: from t_signal import t_signal
"""
import numpy as np

def shrink_to_expand(vol, look=5):
    """T1 缩转放: 前 look 日缩量(连续收缩或 z<0) 且 当日放量(>前5日均量*1.2 或 z>0)"""
    v = vol.values.astype(float)
    if len(v) < look + 6: return False
    prev = v[-(look+1):-1]
    shrink = bool(prev[-1] <= prev[0])   # 前段整体缩量
    expand = bool(v[-1] > v[-6:-1].mean() * 1.2)   # 当日放量
    return bool(shrink and expand)

def yellow_line(ser, ma=5):
    """T2 黄线(日级近似): 收盘 >= MA(ma)。跌破=风险信号。返回 (in_ok, ma_val)"""
    c = ser.values.astype(float)
    if len(c) < ma + 1: return False, None
    m = float(c[-ma-1:-1].mean())   # 昨日 MA(用历史避免当日污染)
    return bool(c[-1] >= m), m

def t_sell(ser, vol, look=10):
    """T出条件(7-29): 放量反弹→高点后停止放量→当前未破前高且无量 → T出点
    日级近似: 近look日高点 → 高点后缩量(日均量<高点前80%) → 当前close未破前高*1.005"""
    c = ser.values.astype(float); v = vol.values.astype(float)
    if len(c) < look + 6 or len(v) < look + 6: return False
    recent = c[-(look+5):-1]
    hi = float(recent.max()); hi_idx = int(len(recent) - 1 - np.argmax(recent[::-1]))
    if hi_idx < 2: return False
    vol_after = float(v[-(look+5):-1][hi_idx+1:].mean()) if hi_idx + 1 < len(recent) else 0.0
    vol_before = float(v[-(look+5):-1][:hi_idx].mean())
    no_vol = vol_after < vol_before * 0.8 if vol_before > 0 else False
    not_break = float(c[-1]) < hi * 1.005
    return bool(no_vol and not_break)

def t_signal(ser, vol, params=None):
    """做T信号综合。ser: close, vol: 量能(真实vol或net代理)。
    返回 {T1_缩转放, T2_黄线, T_sell, stage}"""
    p = params or {}
    look = int(p.get("t_look", 5))
    t1 = shrink_to_expand(vol, look)
    yl, ma = yellow_line(ser)
    ts = t_sell(ser, vol, 10)
    if ts:
        stage = "T出点"
    elif t1 and yl:
        stage = "缩转放买点"
    elif t1:
        stage = "缩转放(黄线下,谨慎)"
    else:
        stage = "无T信号"
    return {"T1_缩转放": t1, "T2_黄线": yl, "T_sell": ts, "stage": stage, "ma": round(ma, 2) if ma else None}
