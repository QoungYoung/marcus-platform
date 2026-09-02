# -*- coding: utf-8 -*-
"""
confirm_chain.py — 确定性门槛(买在确定): 狼大确认链 code 化 (P1, 2026-09-02)
================================================================================
狼大完整确认链(2026-02-03 原话): 下跌 → 缩量 → 止跌 → 企稳 → 反抽 → 压力位补量
  → 反弹 → 过颈线 → 加强 → 新高 → 反转
本实现: S1 缩量止跌 → S2 结构到位(双底/低位横盘) → S3 放量突破 → S4 站稳确认;
         F1 突破无量(假突破) F2 回落颈线(证伪) F3 主线不响应 F4 缩量拉避险(诱多)。
输入: close Series(概念/指数), net Series(净流入, 抛压代理), vol Series(量能, 可选)
输出: {stage, signals, desc} — stage: 下跌中/缩量止跌/结构到位/突破候选/确认/证伪
与 position_class 关系: LOW 埋伏候选 + confirm_chain 触发 S3/S4 → 确认入场;
                       仅 S1/S2 → 埋伏候选不重仓(等确认)。
用法: from confirm_chain import confirm_chain
"""
import numpy as np
import pandas as pd

def min15_stand(bars, look=20, stand_n=3):
    """15分钟级站稳(2026-03-17 狼大: 3个15分钟站稳中轨):
    bars: 当日15min K线列表[{trade_time, close,...}] 或 close列表
    最近 stand_n 根收盘 >= 前 look 根收盘均值(BOLL中轨近似) → True"""
    try:
        closes = [float(b["close"]) if isinstance(b, dict) else float(b) for b in bars]
    except Exception:
        return False
    if len(closes) < look + stand_n: return False
    mid = float(sum(closes[-(look + stand_n):-stand_n]) / look)
    return bool(all(c >= mid for c in closes[-stand_n:]))

def mainline_act(hist, dt, state=None, look=5):
    """主线题材响应度 0-1 (F3): 主线主题概念近look日 资金流入比例 + 涨幅正比例 的均值。
    狼大: 真反弹还是诱多 看看主线题材动没动(2026-01-13)。state: main_line_state(main_line字段)"""
    try:
        import fusion_mainline as fm
        mt = (state or {}).get("main_line")
        if not mt or mt not in fm.THEME_CONCEPTS: return None
        names = fm.THEME_CONCEPTS[mt]
        t = pd.Timestamp(dt)
        up_net = 0; up_px = 0; n = 0
        for code, a in hist.items():
            if a["name"] not in names: continue
            n += 1
            net = pd.Series(a["net_amount"], index=pd.to_datetime(a["dates"])).apply(pd.to_numeric, errors="coerce")
            s = net[net.index <= t].dropna()
            w = min(look, len(s))
            if w >= 2 and float(s.iloc[-w:].sum()) > 0: up_net += 1
            ser = pd.Series(a["close"], index=pd.to_datetime(a["dates"])).apply(pd.to_numeric, errors="coerce").dropna().astype(float)
            base = ser[ser.index <= t]
            if len(base) >= look + 1 and float(base.iloc[-1]) > float(base.iloc[-look-1]): up_px += 1
        if n == 0: return None
        return float((up_net / n + up_px / n) / 2)
    except Exception:
        return None

def _vol_series(vol):
    return vol if vol is not None else None

def _net_series(net):
    return net if net is not None else None

def _z20(s):
    v = s.values.astype(float)
    if len(v) < 21 or v[-21:-1].std() == 0:
        return None
    return float((v[-1] - v[-21:-1].mean()) / v[-21:-1].std())

def _pct120(s):
    v = s.values.astype(float)
    if len(v) < 30:
        return None
    w = min(120, len(v))
    return float((v[-w:] <= v[-1]).mean())

def _local_lows(c, span=10, look=60):
    """近 look 日内的波段低点(span 窗口局部最小), 返回 [(idx, price)]"""
    n = len(c)
    lows = []
    for i in range(max(span, n - look), n - 1):
        lo = max(0, i - span); hi = min(n, i + span + 1)
        if c[i] == c[lo:hi].min() and c[i] <= c[i - 1] and c[i] <= c[i + 1]:
            lows.append((i, float(c[i])))
    ded = []
    for i, v in lows:
        if ded and i - ded[-1][0] <= 5:
            if v <= ded[-1][1]:
                ded[-1] = (i, v)
        else:
            ded.append((i, v))
    return ded

def confirm_chain(ser, net=None, vol=None, params=None, mainline_act=None, hedge_act=None):
    """狼大确认链评估。ser: close Series(≥80点)。
    mainline_act: 主线题材响应度 0-1(可选) — F3: 真反弹看主线题材动没动(2026-01-13)
    hedge_act: 避险方向响应度 0-1(可选) — F4: 缩量拉避险(银行/贵金属)=诱多(2026-08-05)
    返回 {stage, signals{...}, desc}"""
    p = params or {}
    W_NEW_HIGH = int(p.get("new_high_w", 20))      # 突破=创 N 日新高
    STAND_N = int(p.get("stand_days", 3))          # 站稳天数
    HOLD_BACK = float(p.get("hold_back", 0.97))    # 站稳=收盘不低于突破日 97%
    BOX_NARROW = float(p.get("box_narrow", 0.15))  # 横盘箱体宽度阈值
    ML_ACT_TH = float(p.get("mainline_act_th", 0.3))  # F3: 主线响应度低于此=诱多证伪
    HEDGE_ACT_TH = float(p.get("hedge_act_th", 0.6))  # F4: 避险响应度高于此=诱多证伪
    c = ser.values.astype(float)
    n = len(c)
    out = {"stage": "下跌中", "signals": {}, "desc": "下跌/无确认信号"}
    if n < 60:
        return out
    last = float(c[-1])
    # ---- S1 缩量止跌 ----
    # 价格: 最近5日不再创新低(相对前20日低点) 且 r20 跌幅收窄(>-12%)
    low20 = float(c[-25:-5].min()) if n >= 25 else float(c[:-5].min())
    price_stop = last >= low20 * 0.99 and last > float(c[-5:].min()) * 0.995
    r20 = (last / float(c[-21]) - 1) * 100 if n >= 21 and c[-21] > 0 else 0
    # 量能: vol 收缩(z20<0) 或 net 抛压减弱(最近5日净流入均值 > 前5日)
    vol_shrink = None; net_stop = None
    if vol is not None and len(vol) >= 30:
        vz = _z20(vol)
        vol_shrink = bool(vz is not None and vz < 0)
    if net is not None and len(net) >= 15:
        nv = net.values.astype(float)
        recent = float(nv[-5:].mean()); prev = float(nv[-10:-5].mean())
        net_stop = bool(recent > prev)  # 抛压减弱(净流出收窄/转流入)
    quant_stop = (vol_shrink if vol_shrink is not None else True) or (net_stop if net_stop is not None else False)
    s1 = bool(price_stop and quant_stop)
    out["signals"]["S1_缩量止跌"] = s1
    out["signals"]["price_stop"] = bool(price_stop)
    out["signals"]["vol_shrink"] = vol_shrink
    out["signals"]["net_stop"] = net_stop
    if not s1:
        out["desc"] = "S1未满足: 价格或量能仍在下行"
        return out
    out["stage"] = "缩量止跌"
    out["desc"] = "S1缩量止跌: 价格止跌+量能收缩/抛压减弱"
    # ---- S2 结构到位: 双底 或 低位横盘 ----
    lows = _local_lows(c)
    double_bottom = False
    if len(lows) >= 2:
        l1, l2 = lows[-2], lows[-1]
        if l1[1] > 0 and 0.95 <= l2[1] / l1[1] <= 1.05 and 5 <= l2[0] - l1[0] <= 40:
            double_bottom = True
    box30 = c[-30:]; bhi, blo = float(box30.max()), float(box30.min())
    box_narrow = (bhi - blo) / blo < BOX_NARROW if blo > 0 else False
    hi1y = float(c[-250:].max()) if n >= 250 else float(c.max())
    low_pos = last < hi1y * 0.80  # 距 1 年高 -20% 以下(低位)
    flat_low = bool(box_narrow and low_pos)
    s2 = bool(double_bottom or flat_low)
    out["signals"]["S2_结构到位"] = s2
    out["signals"]["double_bottom"] = double_bottom
    out["signals"]["flat_low"] = flat_low
    if not s2:
        out["desc"] = "S2未满足: 无双底且非低位横盘"
        return out
    out["stage"] = "结构到位"
    out["desc"] = "S2结构到位: " + ("双底" if double_bottom else "低位横盘")
    # ---- S3 放量突破 + S4 站稳确认: 从后往前找最近一次突破日, 检查后续站稳 ----
    # (2026-09-02 重构: 支持扫描场景——突破日不定, 找最近一次'创N日新高+量能放大'再查站稳)
    def _quant_up(i):
        """i 日量能放大判定"""
        if vol is not None and len(vol) >= 30:
            vv = vol.values.astype(float)
            if i >= 21 and vv[i-21:i].std() > 0:
                vz = (vv[i] - vv[i-21:i].mean()) / vv[i-21:i].std()
                if vz > 1.5: return True
        if net is not None and len(net) >= 15:
            nv = net.values.astype(float)
            if i >= 6:
                prev = nv[max(0, i-6):i].mean()
                if nv[i] > 0 and (prev == 0 or abs(float(nv[i])) > abs(float(prev))): return True
        return False
    breakout_day = None
    for bk in range(n - 1, max(n - 25, 0), -1):
        if bk >= W_NEW_HIGH:
            hi_bk = float(c[max(0, bk - W_NEW_HIGH):bk].max())
            if c[bk] > hi_bk * 1.005 and _quant_up(bk):
                breakout_day = bk
                break
    out["signals"]["breakout"] = breakout_day is not None
    if breakout_day is None:
        out["stage"] = "证伪" if False else "结构到位"
        out["desc"] = "S3未满足: 近25日无放量突破"
        return out
    # F3 主线响应: 真反弹看主线题材动没动(2026-01-13) — 突破/反弹时主线未响应=诱多证伪
    if mainline_act is not None:
        out["signals"]["mainline_act"] = round(float(mainline_act), 3)
        if mainline_act < ML_ACT_TH:
            out["stage"] = "证伪"
            out["desc"] = "F3主线未响应: 突破/反弹但主线题材没动(%.2f<%.2f)" % (mainline_act, ML_ACT_TH)
            out["signals"]["F3"] = True
            return out
    # F4 诱多识别: 缩量拉避险(银行/贵金属)=诱多(2026-08-05) — 避险资金涌入+突破=诱多
    if hedge_act is not None:
        out["signals"]["hedge_act"] = round(float(hedge_act), 3)
        if hedge_act > HEDGE_ACT_TH:
            out["stage"] = "证伪"
            out["desc"] = "F4诱多: 避险方向(银行/贵金属)资金涌入(%.2f>%.2f), 突破/反弹为诱多" % (hedge_act, HEDGE_ACT_TH)
            out["signals"]["F4"] = True
            return out
    # S4: 突破日后 STAND_N 日站稳(收盘不低于突破日*HOLD_BACK)
    tail = c[breakout_day:min(breakout_day + STAND_N, n)]
    stand = len(tail) >= STAND_N and bool((tail >= float(c[breakout_day]) * HOLD_BACK).all())
    out["signals"]["S3_放量突破"] = True
    out["signals"]["S4_站稳确认"] = stand
    out["signals"]["breakout_day"] = breakout_day
    if stand:
        out["stage"] = "确认"
        out["desc"] = "S4站稳确认: 突破后%d日不破颈线" % STAND_N
    else:
        out["stage"] = "突破候选"
        out["desc"] = "S3放量突破(未站稳): 创%d日新高+量能放大" % W_NEW_HIGH
    return out
